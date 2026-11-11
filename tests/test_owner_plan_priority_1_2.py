"""docs/OWNER_TEST_PLAN.md priority 1 and 2, the lines that no other test proved end to end.

Each test drives the same steps the owner does on screen, through the HTTP API (or the service
behind the screen), with the plan's own numbers: opening float 100, a 3-strip box, a 10% line
discount, credit limit 200 -> 50 -> 20 -> 30, a 10% local contract, a cheque number on the
statement, and so on. Lines already proven elsewhere are listed in the PR's coverage map and are
not repeated here. Hardware (scanner, printer, drawer) and what the eye sees stay manual.
Synthetic data only."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.core import people
from app.inventory import service as inv
from app.sales import channels, contracts
from app.sales.service import local_today
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"
JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
PNG = b"\x89PNG\r\n\x1a\n" + b"owner-plan-transfer-proof" * 4


class PlanCase(APICase):
    """One pharmacy: Panadol (2 strips, 40.00), a 3-strip box (100.00), stock at known cost."""

    def setUp(self):
        super().setUp()
        channels.ensure_defaults(self.db)
        self.today = local_today().isoformat()
        self.expiry = (local_today() + timedelta(days=500)).isoformat()
        cat.add_barcode(self.db, self.ctx, self.pan, "6221025012345")
        self.box = cat.create_item(self.db, self.ctx, {"name_en": "Trial Box 3 strips", "pack_size": 3,
                                                       "public_price": 10000})
        self.stock(self.pan, 20, "15")              # 15.00 per strip, sold at 20.00
        self.stock(self.box, 30, "25")              # 25.00 per strip, sold at 33.33
        self.http_login()

    def stock(self, item, qty, cost, batch="TRIAL-1"):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, item, batch, self.expiry)
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty, batch_id=b,
                              movement_type="purchase", unit_cost=Decimal(cost))
        return b

    def call(self, method, url, expected=200, **kw):
        r = self.c.request(method, url, headers={**H, **kw.pop("headers", {})}, **kw)
        self.assertEqual(r.status_code, expected, r.text)
        return r.json() if "json" in r.headers.get("content-type", "") else r

    def qty(self, item):
        return self.call("GET", f"/api/items/{item}")["stock"]["total_qty"]

    def drawer(self):
        return self.call("GET", "/api/cash/current")["expected_cash"]

    def channel(self, kind):
        return {c["kind"]: c["id"] for c in self.call("GET", "/api/payment-channels")["channels"]}[kind]

    def manual_purchase(self, supplier, invoice_no, lines):
        """The purchase screen's typed invoice: header, lines, then the declared total."""
        r = self.call("POST", "/api/purchases", 201, json={"supplier_id": supplier, "invoice_no": invoice_no})
        inv_id = r["id"] if "id" in r else r["invoice"]["id"]
        d = self.call("GET", f"/api/purchases/{inv_id}")
        for n, line in enumerate(lines):
            d = self.call("POST", f"/api/purchases/{inv_id}/lines", json={"version": d["invoice"]["version"], "line": {
                "expiry_date": self.expiry, "batch_no": f"{invoice_no}-{n}", **line}})
        d = self.call("PATCH", f"/api/purchases/{inv_id}", json={"version": d["invoice"]["version"],
                                                                "changes": {"declared_total": d["invoice"]["computed_total"]}})
        return inv_id, d

    def draft(self, customer=None):
        return self.call("POST", "/api/sales", 201, json={"customer_id": customer})["id"]

    def line(self, sid, item, expected=200, **qty):
        return self.call("POST", f"/api/sales/{sid}/lines", expected, json={"item_id": item, **qty})

    def complete(self, sid, payments, expected=200, **extra):
        d = self.call("GET", f"/api/sales/{sid}")
        f = self.call("POST", f"/api/sales/{sid}/check", json={})["findings"]
        return self.call("POST", f"/api/sales/{sid}/complete", expected, json={
            "payments": payments, "version": d["version"], "request_id": f"plan-{sid}",
            "safety_decisions": {str(i): "تمت المراجعة" for i in range(len(f))}, **extra})

    def sell(self, item, payments=None, customer=None, **qty):
        sid = self.draft(customer)
        d = self.line(sid, item, **qty)
        return self.complete(sid, payments or [{"method": "cash", "amount": d["total"]}])


class TestPriority1(PlanCase):
    def test_same_item_scanned_twice_increases_one_line(self):
        """P1 line 3 (acceptance 35): the second scan of the same barcode raises the first line."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        for n in (1, 2):                                       # two separate scans, not a retry
            found = self.call("GET", "/api/items?q=6221025012345")
            self.assertEqual([x["id"] for x in found], [self.pan])
            d = self.line(sid, found[0]["id"], packs=1, client_ref=f"scan-{n}")
        self.assertEqual(len(d["lines"]), 1)
        self.assertEqual((d["lines"][0]["qty_base"], d["total"]), (4, 8000))
        done = self.complete(sid, [{"method": "cash", "amount": 8000}])
        self.assertEqual(len(done["lines"]), 1)
        self.assertEqual(self.qty(self.pan), 16)

    def test_three_strips_one_by_one_cost_exactly_the_box_price(self):
        """P1 line 4: a box of 3 strips sold strip by strip on one invoice totals the box price, not 99.99."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        totals = [self.line(sid, self.box, units=1, client_ref=f"strip-{n}")["total"] for n in (1, 2, 3)]
        self.assertEqual(totals, [3333, 6666, 10000])
        done = self.complete(sid, [{"method": "cash", "amount": 10000}])
        self.assertEqual((len(done["lines"]), done["lines"][0]["qty_base"], done["total"]), (1, 3, 10000))
        self.assertEqual(self.drawer(), 20000)
        self.assertEqual(self.qty(self.box), 27)

    def test_ten_percent_line_discount_then_cash_with_change_due(self):
        """P1 line 5 (acceptance 5): 10% off a line, cash, the customer hands over 50.00; change 14.00."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.pan, packs=1, discount_bp=1000)
        self.assertEqual((d["subtotal"], d["discount_total"], d["total"]), (4000, 400, 3600))
        done = self.complete(sid, [{"method": "cash", "amount": 3600}], cash_given=5000)
        self.assertEqual((done["total"], done["paid_cash"]), (3600, 3600))
        self.assertEqual(self.drawer(), 10000 + 3600)          # the change never enters the drawer
        page = self.call("GET", f"/receipt/{sid}?given=5000").text
        self.assertIn("خصم 10%", page)
        self.assertRegex(page, r"المدفوع من العميل</td><td>50\.00<")
        self.assertRegex(page, r"الباقي</td><td>14\.00<")
        self.assertRegex(page, r"الإجمالي</td><td>40\.00<")

    def test_zero_stock_item_is_not_added_and_offers_alternatives(self):
        """P1 line 7 (acceptance 37): Enter on an out-of-stock item adds nothing; shortage + similar shown."""
        a = cat.create_item(self.db, self.ctx, {"name_en": "Trial Zero 500mg", "active_ingredient": "paracetamol",
                                                "strength": "500 mg", "dosage_form": "tab", "public_price": 3000,
                                                "pack_size": 1}, force=True)
        b = cat.create_item(self.db, self.ctx, {"name_en": "Trial Other 500mg", "active_ingredient": "paracetamol",
                                                "strength": "500 mg", "dosage_form": "tab", "public_price": 3200,
                                                "pack_size": 1}, force=True)
        self.stock(b, 5, "20", batch="TRIAL-ALT")
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        found = {x["id"]: x for x in self.call("GET", "/api/items?q=Trial Zero")}
        self.assertEqual(found[a]["stock_qty"], 0)
        sid = self.draft()
        r = self.line(sid, a, 409, packs=1)
        self.assertEqual(r["error"]["code"], "insufficient_stock")
        self.assertEqual(self.call("GET", f"/api/sales/{sid}")["lines"], [])
        alt = self.call("GET", f"/api/items/{a}/alternatives")
        self.assertEqual([x["id"] for x in alt["similar"]], [b])
        # the sale screen: an Enter auto-adds only a sellable result; otherwise the shortage badge and the
        # similar/alternative button are drawn (what the owner sees is checked by hand)
        self.assertIn("r.stock_qty > 0", JS)
        self.assertRegex(JS, r"res\.length === 1 && sellable\(res\[0\]\)")
        self.assertIn("✕ ناقص", JS)
        self.assertIn("↔ مثيل / بديل", JS)

    def test_return_one_strip_refunds_what_was_paid_and_receipt_says_return(self):
        """P1 line 10 (acceptance 18): one strip of a discounted pack back for cash; the slip says return."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.pan, packs=1, discount_bp=1000)          # 2 strips: 40.00 - 10% = 36.00
        sale = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        self.assertEqual(sale["total"], 3600)
        r = self.call("POST", f"/api/sales/{sid}/return", json={
            "lines": {sale["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "العميل مش محتاجه"})
        self.assertEqual(r["refund"], 1800)                              # exactly what that strip cost: 20.00 - 10%
        self.assertEqual(self.drawer(), 10000 + 3600 - 1800)
        self.assertEqual(self.qty(self.pan), 19)
        slip = self.call("GET", f"/return-receipt/{r['return_id']}").text
        self.assertIn('<p class="tag">مرتجع</p>', slip)
        self.assertIn(f"إيصال مرتجع — فاتورة رقم {sale['sale_no']}", slip)
        self.assertRegex(slip, r"المبلغ المرتجع</td><td>18\.00<")
        self.assertIn("نقدًا من الخزينة", slip)
        self.assertIn("العميل مش محتاجه", slip)
        inv_page = self.call("GET", f"/api/sales/{sid}")
        self.assertEqual(inv_page["returned_total"], 1800)

    def test_return_one_strip_of_a_three_strip_box_is_within_one_piaster_and_adds_up(self):
        """P1 line 10, the 3-strip box: 90.00 cannot be split into three equal piaster amounts, so the first
        strip back refunds 30.01 and the other two 59.99; together exactly the 90.00 paid (owner: expect 30.01)."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        sid = self.draft()
        d = self.line(sid, self.box, packs=1, discount_bp=1000)          # 100.00 - 10% = 90.00
        sale = self.complete(sid, [{"method": "cash", "amount": d["total"]}])
        line = sale["lines"][0]["id"]
        back = lambda n: self.call("POST", f"/api/sales/{sid}/return", json={    # noqa: E731
            "lines": {line: n}, "refund_method": "cash", "reason": "مرتجع تجربة"})["refund"]
        first, rest = back(1), back(2)
        self.assertEqual((first, rest, first + rest), (3001, 5999, 9000))
        self.assertEqual(self.drawer(), 10000)                           # the drawer is back where it started

    def test_shift_close_expected_is_opening_plus_cash_sales_minus_cash_refunds(self):
        """P1 line 11: expected = 100 + cash sales - cash refunds; card is not in the drawer; a difference needs a reason."""
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        a = self.sell(self.pan, packs=1)                                                         # cash 40.00
        self.sell(self.pan, [{"method": "card", "amount": 4000, "channel_id": self.channel("card")}], packs=1)
        self.call("POST", f"/api/sales/{a['id']}/return", json={
            "lines": {a["lines"][0]["id"]: 1}, "refund_method": "cash", "reason": "مرتجع تجربة"})   # -20.00
        cur = self.call("GET", "/api/cash/current")
        self.assertEqual(cur["expected_cash"], 10000 + 4000 - 2000)
        self.assertEqual(cur["card_total"], 4000)
        r = self.call("POST", "/api/cash/close", 422, json={"counted_cash": 11900})              # 1.00 short, no reason
        self.assertIn("error", r)
        self.assertEqual(self.call("GET", "/api/cash/current")["expected_cash"], 12000)          # still open
        closed = self.call("POST", "/api/cash/close", json={"denominations": {"10000": 1, "1000": 1, "500": 1, "100": 4},
                                                            "note": "نقص جنيه فكة"})
        self.assertEqual((closed["status"], closed["expected_cash"], closed["counted_cash"], closed["difference"]),
                         ("closed", 12000, 11900, -100))

    def test_manual_purchase_of_two_items_adds_quantity_plus_bonus_and_updates_public_price(self):
        """P1 line 12: a typed invoice for two items; approval adds qty + bonus and the new public price."""
        sup = self.db.one("SELECT id FROM suppliers")["id"]
        inv_id, d = self.manual_purchase(sup, "TRIAL-M1", [
            {"item_id": self.pan, "qty": 5, "bonus_qty": 1, "public_price": 4200, "discount_bp": 2000},
            {"item_id": self.box, "qty": 2, "bonus_qty": 0, "public_price": 10000, "discount_bp": 2000}])
        self.assertEqual([l["line_total"] for l in d["lines"]], [16800, 16000])      # 5 x 33.60, 2 x 80.00
        self.assertEqual(d["invoice"]["computed_total"], 32800)
        self.assertTrue(d["can_approve"], d["issues"])
        self.assertEqual((self.qty(self.pan), self.qty(self.box)), (20, 30))        # a draft moves nothing
        d = self.call("POST", f"/api/purchases/{inv_id}/approve", json={"version": d["invoice"]["version"]})
        self.assertEqual(d["invoice"]["status"], "approved")
        self.assertEqual(self.qty(self.pan), 20 + (5 + 1) * 2)                      # quantity + bonus, in strips
        self.assertEqual(self.qty(self.box), 30 + 2 * 3)
        self.assertEqual(self.call("GET", f"/api/items/{self.pan}")["public_price"], 4200)
        self.assertEqual(self.call("GET", f"/api/items/{self.box}")["public_price"], 10000)   # unchanged price kept
        hist = self.db.all("SELECT previous_price, public_price, source FROM item_price_history WHERE item_id=?"
                           " ORDER BY observed_at", (self.pan,))
        self.assertEqual(tuple(hist[-1]), (4000, 4200, "purchase_invoice"))
        self.assertEqual(self.db.one("SELECT sum(amount) FROM supplier_ledger WHERE supplier_id=?", (sup,))[0], 32800)
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])

    def test_damaged_adjustment_shows_in_stock_losses_with_the_user(self):
        """P1 line 13 (acceptance 26): one unit out as damaged; the loss report shows it with who did it."""
        a = self.call("POST", f"/api/items/{self.pan}/adjust", json={"qty_delta": -1, "reason": "damaged"})
        self.assertEqual((a["before"], a["after"]), (20, 19))
        rep = self.call("GET", f"/api/reports/losses?from={self.today}&to={self.today}")
        rows = [r for r in rep["rows"] if r["item_id"] == self.pan]
        self.assertEqual([(r["reason"], r["qty_delta"], r["by_name"], r["value"]) for r in rows],
                         [("damaged", -1, "Owner", -1500)])
        g = {x["reason"]: x for x in rep["groups"]}
        self.assertEqual((g["damaged"]["qty_out"], g["damaged"]["value"]), (1, -1500))
        self.assertEqual(rep["total"], -1500)

    def test_one_section_count_adjusts_stock_with_the_reason_in_reports(self):
        """P1 line 14 (acceptance 28): "one section" count: count one item short, give the reason, approve."""
        sec = self.call("POST", "/api/categories", json={"name": "مسكنات تجربة"})["id"]
        self.call("POST", "/api/categories/assign", json={"item_ids": [self.pan, self.box], "category_id": sec})
        outside = cat.create_item(self.db, self.ctx, {"name_en": "Trial Outside", "public_price": 1000})
        self.stock(outside, 4, "5", batch="TRIAL-O")
        cid = self.call("POST", "/api/counts", 201, json={"title": "جرد قسم المسكنات", "scope": "category",
                                                          "category_id": sec})["id"]
        r = self.call("POST", f"/api/counts/{cid}/entries", 422, json={"item_id": outside, "qty": 4})
        self.assertEqual(r["error"]["code"], "out_of_scope")
        self.call("POST", f"/api/counts/{cid}/entries", json={"item_id": self.pan, "qty": 18})   # 2 strips short
        v = self.call("GET", f"/api/counts/{cid}/variances")
        self.assertEqual([(l["item_id"], l["difference"]) for l in v["lines"]], [(self.pan, -2)])
        self.call("POST", f"/api/counts/{cid}/approve", json={"shortage_reason": "damaged"})
        self.assertEqual((self.qty(self.pan), self.qty(self.box), self.qty(outside)), (18, 30, 4))
        rep = self.call("GET", f"/api/reports/losses?from={self.today}&to={self.today}")
        rows = [r for r in rep["rows"] if r["ref_id"] == cid]
        self.assertEqual([(r["item_id"], r["qty_delta"], r["reason"], r["note"], r["by_name"]) for r in rows],
                         [(self.pan, -2, "damaged", "تسوية جرد", "Owner")])
        self.assertEqual(rep["total"], -3000)                                                  # 2 x 15.00


class TestPriority2(PlanCase):
    def test_credit_limit_200_sale_50_collection_20_balance_30(self):
        """P2 line 1: a trial customer with a 200.00 limit buys 50.00 on credit, pays 20.00, owes 30.00."""
        x = cat.create_item(self.db, self.ctx, {"name_en": "Trial Fifty", "public_price": 5000})
        self.stock(x, 10, "30", batch="TRIAL-50")
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        cust = self.call("POST", "/api/customers", 201, json={"name": "عميل تجربة", "credit_limit": 20000})["id"]
        s = self.sell(x, [{"method": "credit", "amount": 5000}], customer=cust, packs=1)
        self.assertEqual((s["on_credit"], s["paid_cash"]), (5000, 0))
        self.assertEqual(self.drawer(), 10000)                                   # credit is not cash
        pay = self.call("POST", f"/api/customers/{cust}/payments", json={"amount": 2000, "method": "cash"})
        self.assertEqual(pay["balance"], 3000)
        self.assertEqual(self.call("GET", f"/api/customers/{cust}")["balance"], 3000)
        self.assertEqual(self.drawer(), 12000)                                   # the collection is in the drawer
        sid = self.draft(cust)                                                   # 30 + 180 > 200: refused
        self.line(sid, x, packs=4)
        r = self.complete(sid, [{"method": "credit", "amount": 20000}], 409)
        self.assertEqual(r["error"]["code"], "credit_limit")
        self.assertEqual(self.call("GET", f"/api/customers/{cust}")["balance"], 3000)

    def test_local_contract_ten_percent_is_automatic_and_never_below_cost_without_the_owner(self):
        """P2 line 2 (acceptance 24): 10% local contract, linked customer, applied by itself; below cost blocked."""
        k = contracts.save(self.db, self.ctx, {"name": "نقابة تجربة", "discount_local_bp": 1000,
                                               "discount_imported_bp": 1000})[0]["id"]
        cust = self.call("POST", "/api/customers", 201, json={"name": "عضو تجربة"})["id"]
        self.call("POST", f"/api/customers/{cust}/contract", json={"contract_id": k})
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        self.call("POST", "/api/cash/open", 201, json={"opening_cash": 10000})
        self.c.post("/api/auth/logout", headers=H)
        self.http_login("asst", PW)
        sid = self.draft(cust)
        d = self.line(sid, self.pan, packs=1)                                    # nobody typed a discount
        self.assertEqual((d["lines"][0]["discount_bp"], d["lines"][0]["discount_source"], d["total"]),
                         (1000, "contract", 3600))
        self.assertEqual(self.complete(sid, [{"method": "cash", "amount": 3600}])["total"], 3600)
        thin = cat.create_item(self.db, self.ctx, {"name_en": "Trial Thin Margin", "public_price": 1000})
        self.stock(thin, 5, "9.50", batch="TRIAL-T")                             # 10% off 10.00 = 9.00 < 9.50
        sid = self.draft(cust)
        d = self.line(sid, thin, packs=1)
        self.assertEqual(d["total"], 900)
        r = self.complete(sid, [{"method": "cash", "amount": 900}], 403)        # the assistant cannot sell at a loss
        self.assertEqual(r["error"]["details"].get("permission"), "sales.below_cost", r)
        self.c.post("/api/auth/logout", headers=H)
        self.http_login()
        r = self.complete(sid, [{"method": "cash", "amount": 900}], 409)        # the owner must confirm it
        self.assertEqual(r["error"]["code"], "below_cost_confirm")
        self.assertEqual(self.qty(thin), 5)
        done = self.complete(sid, [{"method": "cash", "amount": 900}], confirm_below_cost=True)
        self.assertEqual(done["status"], "completed")

    def test_transfer_sale_keeps_its_image_and_warns_on_a_duplicate_upload(self):
        """P2 line 4 (acceptance 17): InstaPay sale + screenshot; the same screenshot on another invoice warns."""
        ch = self.channel("transfer")
        s1 = self.sell(self.pan, [{"method": "transfer", "amount": 4000, "channel_id": ch}], packs=1)
        s2 = self.sell(self.pan, [{"method": "transfer", "amount": 4000, "channel_id": ch}], packs=1)
        self.assertEqual((s1["paid_transfer"], s1["paid_cash"]), (4000, 0))
        up = lambda sid: self.call("POST", f"/api/sales/{sid}/attachments", data={"note": "إنستاباي"},  # noqa: E731
                                   files={"file": ("shot.png", PNG, "image/png")})
        r1 = up(s1["id"])
        self.assertEqual(len(r1["attachments"]), 1)
        self.assertFalse(r1.get("duplicate_of"))
        r2 = up(s2["id"])
        self.assertEqual(r2["duplicate_of"], s1["sale_no"])
        kinds = [n["kind"] for n in self.call("GET", "/api/notifications")["items"]]
        self.assertIn("transfer_duplicate", kinds)
        att = self.call("GET", f"/api/sales/{s1['id']}")["attachments"]
        self.assertEqual(len(att), 1)
        img = self.c.get(f"/api/attachments/{att[0]['id']}")
        self.assertEqual((img.status_code, img.content), (200, PNG))
        rep = self.call("GET", f"/api/reports/transfers?from={self.today}&to={self.today}")
        self.assertEqual({r["sale_no"]: (r["proofs"], r["state"]) for r in rep["rows"]},
                         {s1["sale_no"]: (1, "pending"), s2["sale_no"]: (1, "pending")})
        self.assertEqual(rep["totals"]["all"], 8000)

    def test_supplier_cheque_payment_shows_with_its_number_on_the_period_statement(self):
        """P2 line 6 (acceptance 19): pay by cheque; the period statement lists it with the cheque number."""
        from app.procurement import suppliers as sup_mod
        sup = self.db.one("SELECT id FROM suppliers")["id"]
        inv_id, d = self.manual_purchase(sup, "TRIAL-C1", [
            {"item_id": self.box, "qty": 2, "bonus_qty": 0, "public_price": 10000, "discount_bp": 2500}])
        self.call("POST", f"/api/purchases/{inv_id}/approve", json={"version": d["invoice"]["version"]})   # 150.00 owed
        r = self.call("POST", f"/api/suppliers/{sup}/pay", 422, json={"amount": 5000, "method": "cheque"})
        self.assertIn("error", r)                                                # a cheque needs its number
        self.call("POST", f"/api/suppliers/{sup}/pay", json={"amount": 5000, "method": "cheque", "ref": "TRIAL-000123"})
        st = self.call("GET", f"/api/suppliers/{sup}/statement?from={self.today}&to={self.today}")
        pay = [l for l in st["lines"] if l["kind"] == "payment"]
        self.assertEqual([(l["amount"], l["pay_method"], l["pay_ref"]) for l in pay], [(-5000, "cheque", "TRIAL-000123")])
        self.assertEqual((st["purchases"], st["payments"], st["closing"]), (15000, 5000, 10000))
        self.assertEqual(sup_mod.balance(self.db, self.ctx.org_id, sup), 10000)
