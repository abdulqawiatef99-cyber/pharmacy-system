"""Part 3 item 6a: a sale return and its replacement sale as one operation.

Three totals (returned value, new sale, net), the return follows every return rule, the new sale is paid
first from where the refund went so only the net moves, both or neither are written, a retry never posts
twice, and the returned goods are a sale return in stock, never a purchase."""
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.inventory import service as inv
from app.sales.cash import CashDrawer
from app.sales.service import Sales
from tests.helpers import DBTestCase


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        CashDrawer(self.db).open(self.ctx, 0)
        self.s = Sales(self.db)
        self.a = self.stock("Alpha tab", 1000)
        self.b = self.stock("Beta tab", 1500)
        self.c = self.stock("Gamma tab", 400)

    def stock(self, name, price):
        iid = cat.create_item(self.db, self.ctx, {"name_en": name, "pack_size": 1, "public_price": price})
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=iid, qty_delta=20, movement_type="opening",
                              unit_cost=Decimal("3"))
        return iid

    def sell(self, item, units, customer=None, discount_bp=0, **pay):
        sid = self.s.create(self.ctx, customer)
        self.s.add_line(self.ctx, sid, item, units=units)
        if discount_bp:
            lid = self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (sid,))["id"]
            self.s.update_line(self.ctx, sid, lid, discount_bp=discount_bp)
        total = self.s.get(self.ctx, sid)["total"]
        self.s.complete(self.ctx, sid, **(pay or {"paid_cash": total}))
        return sid

    def line(self, sid):
        return self.db.one("SELECT id FROM sale_lines WHERE sale_id=?", (sid,))["id"]

    def draft(self, item, units):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, item, units=units)
        return sid

    def cash_sum(self):
        return self.db.one("SELECT coalesce(sum(amount),0) t FROM cash_movements")["t"]

    def qty(self, item):
        return self.db.one("SELECT coalesce(sum(qty_delta),0) q FROM stock_movements WHERE item_id=?", (item,))["q"]


class TestExchange(Base):
    def test_cash_three_totals_and_only_the_net_moves(self):
        sid = self.sell(self.a, 2)
        cash0 = self.cash_sum()
        new = self.draft(self.b, 1)
        r = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="cash", reason="العميل غير الصنف")
        self.assertEqual((r["returned"], r["new_total"], r["net"]), (1000, 1500, 500))
        self.assertEqual(r["settled"], {"pay": 500, "refund": 0})
        self.assertEqual(self.cash_sum() - cash0, 500)
        self.assertEqual((self.qty(self.a), self.qty(self.b)), (19, 19))
        kinds = {m["movement_type"] for m in self.db.all("SELECT movement_type FROM stock_movements WHERE item_id=?", (self.a,))}
        self.assertEqual(kinds, {"opening", "sale", "sale_return"})      # never a purchase
        self.assertEqual(r["sale"]["status"], "completed")
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='sale.exchange'"))

    def test_cheaper_replacement_gives_the_difference_back(self):
        sid = self.sell(self.a, 2)
        cash0 = self.cash_sum()
        r = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, self.draft(self.c, 1), refund_method="cash",
                            reason="أرخص")
        self.assertEqual((r["returned"], r["new_total"], r["net"], r["settled"]["refund"]), (1000, 400, -600, 600))
        self.assertEqual(self.cash_sum() - cash0, -600)

    def test_credit_sale_nets_on_the_account_and_pays_the_rest_in_cash(self):
        cust = self.s.create_customer(self.ctx, name="عميل آجل", credit_limit=100000)
        sid = self.sell(self.a, 2, customer=cust, on_credit=2000)
        cash0 = self.cash_sum()
        new = self.draft(self.b, 1)
        r = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="credit", pay_method="cash",
                            reason="استبدال")
        self.assertEqual(self.s.balance(self.ctx, cust), 2000)          # -1000 return, +1000 offset: unchanged
        self.assertEqual(self.cash_sum() - cash0, 500)
        self.assertEqual(r["sale"]["customer_id"], cust)                # the replacement is the same customer's

    def test_cash_refund_of_a_credit_sale_is_refused_and_nothing_is_written(self):
        cust = self.s.create_customer(self.ctx, name="عميل", credit_limit=100000)
        sid = self.sell(self.a, 2, customer=cust, on_credit=2000)
        new = self.draft(self.b, 1)
        with self.assertRaises(Conflict):
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="cash", reason="استبدال")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))
        self.assertEqual(self.s.get(self.ctx, new)["status"], "draft")
        self.assertEqual(self.qty(self.a), 18)

    def test_both_or_neither_when_the_replacement_fails(self):
        sid = self.sell(self.a, 2)
        empty = self.s.create(self.ctx)
        with self.assertRaises(ValidationFailed):
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, empty, refund_method="cash", reason="استبدال")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))
        self.assertEqual(self.qty(self.a), 18)

    def test_a_retry_with_the_same_request_posts_nothing_twice(self):
        sid = self.sell(self.a, 2)
        new = self.draft(self.b, 1)
        args = dict(refund_method="cash", reason="استبدال", request_id="req-1")
        first = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, **args)
        again = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, **args)
        self.assertTrue(again["replayed"])
        self.assertEqual((again["returned"], again["net"]), (first["returned"], first["net"]))
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_returns")["n"], 1)
        self.assertEqual(self.qty(self.a), 19)

    def test_the_return_value_is_after_the_line_discount(self):
        sid = self.sell(self.a, 2, discount_bp=1000)                    # 2 x 10.00 at 10% = 18.00
        r = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, self.draft(self.b, 1), refund_method="cash",
                            reason="استبدال")
        self.assertEqual(r["returned"], 900)

    def test_refusals(self):
        sid = self.sell(self.a, 2)
        other = self.s.create_customer(self.ctx, name="تاني", credit_limit=0)
        cust = self.s.create_customer(self.ctx, name="الأصلي", credit_limit=0)
        sid2 = self.sell(self.a, 1, customer=cust)
        new = self.s.create(self.ctx, other)
        self.s.add_line(self.ctx, new, self.b, units=1)
        with self.assertRaises(ValidationFailed) as e:
            self.s.exchange(self.ctx, sid2, {self.line(sid2): 1}, new, refund_method="cash", reason="استبدال")
        self.assertEqual(e.exception.code, "customer_mismatch")
        with self.assertRaises(ValidationFailed):
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, sid, refund_method="cash", reason="استبدال")
        with self.assertRaises(ValidationFailed):
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="bitcoin", reason="استبدال")
        with self.assertRaises(NotFound):
            self.s.exchange(self.ctx, "nope", {self.line(sid): 1}, new, refund_method="cash", reason="استبدال")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))


class TestFindForReturn(Base):
    def test_by_invoice_item_code_and_customer_with_quantities(self):
        cust = self.s.create_customer(self.ctx, name="منى للبحث", phone="01000000001", credit_limit=0)
        sid = self.sell(self.a, 3, customer=cust)
        self.s.return_items(self.ctx, sid, {self.line(sid): 1}, refund_method="cash", reason="مرتجع أول")
        no = self.s.get(self.ctx, sid)["sale_no"]
        with self.db.transaction():
            self.db.execute("UPDATE items SET code='A100' WHERE id=?", (self.a,))
        code = "A100"
        for q in (str(no), code, "01000000001", "منى للبحث"):
            got = self.s.find_for_return(self.ctx, q)
            self.assertIn(sid, [g["id"] for g in got], q)
        g = next(x for x in self.s.find_for_return(self.ctx, "01000000001") if x["id"] == sid)
        self.assertEqual((g["lines"][0]["sold"], g["lines"][0]["returned"], g["lines"][0]["returnable"]), (3, 1, 2))
        self.assertTrue(g["in_window"])
        self.assertEqual(g["window_days"], 7)
        self.assertEqual(self.s.find_for_return(self.ctx, "  "), [])


class TestExchangeAPI(Base):
    def test_route_and_replay(self):
        from dataclasses import replace
        from starlette.testclient import TestClient
        from app import config
        from app.api.app import create_app
        from app.purchasing.service import Purchasing
        from tests.helpers import OWNER_PW
        st = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                     backup_dir=self.tmp / "backups")
        c = TestClient(create_app(self.db, st, Purchasing(self.db, st)))
        H = {"X-Requested-With": "pharm"}
        self.assertEqual(c.post("/api/auth/login", json={"username": "owner", "password": OWNER_PW}, headers=H).status_code, 200)
        sid = self.sell(self.a, 2)
        new = self.draft(self.b, 1)
        body = {"lines": {self.line(sid): 1}, "new_sale_id": new, "refund_method": "cash", "reason": "استبدال",
                "request_id": "x1"}
        r = c.post(f"/api/sales/{sid}/exchange", json=body, headers={**H, "Idempotency-Key": "e1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["returned"], r.json()["new_total"], r.json()["net"]), (1000, 1500, 500))
        c.post(f"/api/sales/{sid}/exchange", json=body, headers={**H, "Idempotency-Key": "e1"})
        self.assertEqual(self.db.one("SELECT count(*) n FROM sale_returns")["n"], 1)
        f = c.get("/api/returns/find?q=" + str(self.s.get(self.ctx, sid)["sale_no"]), headers=H).json()["sales"]
        self.assertEqual(f[0]["lines"][0]["returned"], 1)


class TestPreview(Base):
    def test_preview_writes_nothing_and_matches_the_real_exchange(self):
        sid = self.sell(self.a, 2, discount_bp=1000)
        before = {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in
                  ("sale_returns", "stock_movements", "cash_movements", "audit_log", "notifications")}
        v = self.s.return_value(self.ctx, sid, {self.line(sid): 1}, refund_method="cash")
        self.assertEqual(before, {t: self.db.one(f"SELECT count(*) FROM {t}")[0] for t in before})
        r = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, self.draft(self.b, 1), refund_method="cash",
                            reason="استبدال")
        self.assertEqual(v, r["returned"])


class TestCodexReview396(Base):
    def test_a_delivery_replacement_is_refused(self):
        sid = self.sell(self.a, 2)
        new = self.draft(self.b, 1)
        self.s.set_details(self.ctx, new, {"kind": "delivery", "delivery": {"address": "شارع", "phone": "0100"}}) \
            if True else None
        with self.assertRaises(ValidationFailed) as e:
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="cash", reason="استبدال")
        self.assertEqual(e.exception.code, "exchange_delivery")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))

    def test_a_card_refund_is_not_offered_in_an_exchange(self):
        sid = self.sell(self.a, 2, paid_card=2000)
        with self.assertRaises(ValidationFailed):
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, self.draft(self.b, 1), refund_method="card",
                            reason="استبدال")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))


class TestCodexReview397(Base):
    def test_preview_totals_come_from_one_simulation_and_match_the_exchange(self):
        sid = self.sell(self.a, 2)
        new = self.draft(self.a, 1)                       # the returned item is also the replacement
        r, n = self.s.return_value(self.ctx, sid, {self.line(sid): 1}, refund_method="cash", new_sid=new)
        done = self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="cash", reason="استبدال",
                               expected_new_total=n)
        self.assertEqual((done["returned"], done["new_total"]), (r, n))

    def test_a_replacement_total_other_than_the_one_shown_is_refused(self):
        sid = self.sell(self.a, 2)
        new = self.draft(self.b, 1)
        with self.assertRaises(Conflict) as e:
            self.s.exchange(self.ctx, sid, {self.line(sid): 1}, new, refund_method="cash", reason="استبدال",
                            expected_new_total=1499)
        self.assertEqual(e.exception.code, "price_changed")
        self.assertFalse(self.db.one("SELECT 1 FROM sale_returns"))


class TestExchangeReceiptFallback(__import__("unittest").TestCase):
    """Codex on #396: after an exchange a browser / failed receipt prints in the page (the receipt URL)."""

    def test_browser_and_failed_print_in_the_page(self):
        import json, re, shutil, subprocess
        from pathlib import Path
        if not shutil.which("node"):
            self.skipTest("Node not available")
        js = (Path(__file__).resolve().parents[1] / "app" / "web" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"\nfunction exchangeDevices\(.*?\n\}\n", js, re.S).group(0)
        run = fn + """
const printed = [], toasts = [];
function printInFrame(u) { printed.push(u); }
function toast(m, bad) { toasts.push([m, !!bad]); }
for (const st of ["browser", "failed", "sent", undefined]) exchangeDevices({ sale_id: "S1", devices: st ? { receipt: st, message: st === "failed" ? "x" : null } : undefined });
console.log(JSON.stringify({ printed, toasts }));
"""
        r = subprocess.run([shutil.which("node"), "-e", run], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"printed": ["/receipt/S1", "/receipt/S1"], "toasts": [["x", True]]})
        self.assertIn("exchangeDevices(r);", js)
        self.assertIn("expected_new_total: got.t.new_total", js)
        self.assertIn("findings = []; if ($(\"#exSafe\")) $(\"#exSafe\").innerHTML = \"\";", js)   # cleared on a change
