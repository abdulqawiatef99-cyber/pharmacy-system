"""U4-2: the purchases tool rebuilt on demand.monthly() (synthetic data only).

Owner's rules (the weekly-order playbook): regular = sold in >= 6 of the last 12 months and 2 of the last 3;
semi-regular = 4-5 of 12; monthly average = max(avg of last 3, avg of last 6); qty = ceil(avg x days/30 - stock
- arrived not entered - on order); week / 2 weeks / month; medicines only unless asked; customer requests and the
shortage book always in; owner-OK flags; split per supplier by the best real discount; order tracking; outputs."""
import io
from dataclasses import replace
from datetime import date, timedelta
from unittest import mock

from app.catalog import card
from app.catalog import service as cat
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.db import new_id
from app.inventory import service as inv
from app.procurement import demand
from app.procurement import purchase_plan as pp
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

TODAY = date(2026, 11, 22)
MONTHS12 = [f"{y:04d}-{m:02d}" for y, m in
            [(2025, 11), (2025, 12)] + [(2026, k) for k in range(1, 11)]]      # the 12 complete months before Nov 2026


class PlanBase(DBTestCase):
    def setUp(self):
        super().setUp()
        demand._cache.clear()
        self.s1 = cat.create_supplier(self.db, self.ctx, name="مورد أ", base_discount_local_bp=2000)
        self.s2 = cat.create_supplier(self.db, self.ctx, name="مورد ب")

    def item(self, name, **kw):
        return cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": kw.pop("public_price", 5000), **kw})

    def months(self, item, units_by_month: dict):
        with self.db.transaction():
            for mo, u in units_by_month.items():
                y, m = int(mo[:4]), int(mo[5:])
                end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).isoformat()
                self.db.execute("INSERT INTO consumption_history (id, org_id, branch_id, item_id, period_start, period_end,"
                                " qty_base, source) VALUES (?,?,?,?,?,?,?, 'manual')",
                                (new_id(), self.ctx.org_id, self.ctx.branch_id, item, f"{mo}-01", end, u))

    def stock(self, item, qty):
        with self.db.transaction():
            inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=qty, movement_type="adjustment",
                              ref_type="test", ref_id=new_id())

    def buy(self, item, supplier, *, public=5000, net=4000, qty=10, bonus=0, days_ago=10, status="approved",
            raw="x", match="confirmed", method="manual"):
        pid = new_id()
        at = (TODAY - timedelta(days=days_ago)).isoformat() + "T10:00:00.000Z"
        with self.db.transaction():
            self.db.execute("INSERT INTO purchase_invoices (id, org_id, branch_id, supplier_id, invoice_no, invoice_date,"
                            " status, source_type, approved_at, created_at) VALUES (?,?,?,?,?,?,?,'manual',?,?)",
                            (pid, self.ctx.org_id, self.ctx.branch_id, supplier, pid, at[:10], status,
                             at if status == "approved" else None, at))
            self.db.execute("INSERT INTO purchase_invoice_lines (id, invoice_id, org_id, line_no, raw_name, item_id, qty,"
                            " bonus_qty, public_price, unit_net_price, match_status, match_method)"
                            " VALUES (?,?,?,1,?,?,?,?,?,?,?,?)",
                            (new_id(), pid, self.ctx.org_id, raw, item, qty, bonus, public, net, match, method))
        return pid

    def plan(self, ctx=None, **kw):
        return pp.plan(self.db, ctx or self.ctx, today=TODAY, **kw)

    def rows(self, **kw):
        return {r["item_id"]: r for r in self.plan(**kw)["rows"]}


class TestRules(PlanBase):
    def test_classify(self):
        def c(sold):          # 12 booleans, oldest first
            return pp.classify([10 if s else 0 for s in sold])
        self.assertEqual(c([1] * 12), "regular")
        self.assertEqual(c([1] * 4 + [0] * 6 + [1, 1]), "regular")          # 6 of 12, 2 of last 3
        self.assertEqual(c([1] * 9 + [0, 0, 1]), "irregular")              # 10 of 12 but only 1 of last 3
        self.assertEqual(c([0] * 7 + [1] * 4 + [0]), "semi")               # 4 of 12
        self.assertEqual(c([0] * 7 + [1] * 5), "semi")                     # 5 of 12 (even with 3 of the last 3)
        self.assertEqual(c([0] * 9 + [1] * 3), "irregular")                # 3 of 12
        self.assertEqual(c([0] * 12), "dead")

    def test_monthly_average_is_the_larger_of_3_and_6(self):
        u = [0] * 6 + [60, 60, 60, 0, 0, 30]        # last 6 = 210/6 = 35, last 3 = 30/3 = 10
        self.assertEqual(pp.monthly_avg(u), 35)
        u = [0] * 6 + [0, 0, 0, 30, 30, 30]         # last 3 = 30 > last 6 = 15
        self.assertEqual(pp.monthly_avg(u), 30)

    def test_quantity_formula_in_packs(self):
        # avg 35 units a month, a week: 35*7/30 = 8.17 units - stock 2 - arrived 1 - on order 0 = 5.17 -> 2 packs of 3
        self.assertEqual(pp.packs_for(35, 7, stock=2, arrived=1, on_order=0, pack=3), 2)
        self.assertEqual(pp.packs_for(35, 7, stock=9, arrived=0, on_order=0, pack=3), 0)
        self.assertEqual(pp.packs_for(30, 30, stock=0, arrived=0, on_order=30, pack=1), 0)
        self.assertEqual(pp.packs_for(30, 30, stock=-5, arrived=0, on_order=0, pack=1), 30)     # negative stock = 0


class TestPlan(PlanBase):
    def setUp(self):
        super().setUp()
        self.reg = self.item("Regular Tab")
        self.months(self.reg, {m: 30 for m in MONTHS12})                  # 30 a month, every month
        self.semi = self.item("Semi Syrup")
        self.months(self.semi, {m: 6 for m in MONTHS12[-4:]})             # 4 of 12, sold in the last 3
        self.rare = self.item("Rare Cream")
        self.months(self.rare, {MONTHS12[-1]: 5, MONTHS12[3]: 5})         # 2 of 12: irregular
        self.soap = self.item("Soap Bar", is_drug=0)
        self.months(self.soap, {m: 30 for m in MONTHS12})                 # regular but a supply
        self.asked = self.item("Asked Drops")
        card.set_shortage(self.db, self.ctx, self.asked, True)

    def test_default_shows_regular_semi_and_asked_only(self):
        out = self.plan()
        ids = {r["item_id"] for r in out["rows"]}
        self.assertEqual(ids, {self.reg, self.semi, self.asked})
        r = {x["item_id"]: x for x in out["rows"]}
        self.assertEqual(r[self.reg]["class"], "regular")
        self.assertEqual(r[self.semi]["class"], "semi")
        self.assertEqual(out["months"], MONTHS12)
        # 30 a month: week = ceil(7) = 7, 2 weeks = 14, month = 30
        self.assertEqual(r[self.reg]["qty"], {"7": 7, "14": 14, "30": 30})
        self.assertEqual(r[self.asked]["qty"]["7"], 1)                   # asked, empty shelf, never sold: one box
        self.assertTrue(r[self.asked]["asked"])
        self.assertEqual(out["period_default"], 7)

    def test_supplies_only_when_asked_for(self):
        self.assertIn(self.soap, self.rows(supplies=True))
        card.set_shortage(self.db, self.ctx, self.soap, True)
        self.assertIn(self.soap, self.rows())                            # the shortage book always counts

    def test_scope_all_shows_irregular(self):
        r = self.rows(scope="all")
        self.assertEqual(r[self.rare]["class"], "irregular")
        self.assertFalse(r[self.rare]["shown"])

    def test_stock_drafts_and_sent_orders_reduce_the_quantity(self):
        self.stock(self.reg, 5)
        self.buy(self.reg, self.s1, qty=3, status="draft", days_ago=0)       # arrived, entered, not approved
        pp.create_order(self.db, self.ctx, supplier_id=self.s2, lines=[{"item_id": self.reg, "packs": 4}], now=TODAY)
        r = self.rows()[self.reg]
        self.assertEqual((r["on_hand"], r["arrived"], r["on_order"]), (5, 3, 4))
        self.assertEqual(r["qty"]["30"], 30 - 5 - 3 - 4)
        self.assertEqual(r["qty"]["7"], 0)
        self.assertIn(self.reg, self.rows())                             # still in: the month column needs it

    def test_an_item_covered_for_a_month_leaves_the_list(self):
        self.stock(self.reg, 100)
        self.assertNotIn(self.reg, self.rows())

    def test_expired_stock_does_not_count(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.reg, "OLD", "2026-01-01")
            inv.post_movement(self.db, self.ctx, item_id=self.reg, qty_delta=40, movement_type="adjustment",
                              ref_type="test", ref_id=new_id(), batch_id=b)
        self.assertEqual(self.rows()[self.reg]["on_hand"], 0)

    def test_tenant_isolation(self):
        _, other = self.second_tenant()
        self.assertEqual(pp.plan(self.db, other, today=TODAY)["rows"], [])

    def test_needs_purchases_read(self):
        ctx = replace(self.ctx, permissions=self.ctx.permissions - {"purchases.read"})
        with self.assertRaises(Forbidden):
            self.plan(ctx=ctx)


class TestSuppliersAndFlags(PlanBase):
    def setUp(self):
        super().setUp()
        self.it = self.item("Split Tab", public_price=10000)
        self.months(self.it, {m: 30 for m in MONTHS12})

    def test_best_real_discount_with_the_bonus_and_every_supplier_side_by_side(self):
        self.buy(self.it, self.s1, public=10000, net=8000, qty=10, days_ago=20)          # 20%
        self.buy(self.it, self.s2, public=10000, net=8500, qty=10, bonus=2, days_ago=5)  # 1 - 85000/120000 = 29.17%
        r = self.rows()[self.it]
        sups = {s["supplier_id"]: s["discount_bp"] for s in r["suppliers"]}
        self.assertEqual(sups, {self.s1: 2000, self.s2: 2917})
        self.assertEqual(r["best"]["supplier_id"], self.s2)
        self.assertEqual(r["last"]["supplier_id"], self.s2)

    def test_flags_for_the_owner(self):
        dear = self.item("Dear Pen", public_price=30000)
        self.months(dear, {m: 3 for m in MONTHS12})
        self.buy(dear, self.s1, public=30000, net=25500, qty=1, days_ago=40)              # 15% < official 20%
        r = self.rows()[dear]
        self.assertIn("expensive", r["flags"])
        self.assertIn("below_official", r["flags"])
        self.buy(self.it, self.s1, public=10000, net=7500, qty=5, days_ago=60)            # 25% before
        self.buy(self.it, self.s1, public=10000, net=8000, qty=5, days_ago=5)             # 20% now
        r = self.rows()[self.it]
        self.assertIn("below_last", r["flags"])
        self.assertEqual(r["best"]["prev_bp"], 2500)

    def test_fridge_and_unsure_match(self):
        cid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO categories (id, org_id, name, mark) VALUES (?,?,?, 'fridge')",
                            (cid, self.ctx.org_id, "ثلاجة"))
            self.db.execute("UPDATE items SET category_id=? WHERE id=?", (cid, self.it))
        self.buy(self.it, self.s1, raw="SPLT TB", match="suggested", method="fuzzy")
        out = self.plan()
        r = {x["item_id"]: x for x in out["rows"]}[self.it]
        self.assertIn("fridge", r["flags"])
        self.assertIn("unsure_match", r["flags"])
        self.assertEqual([c["item_id"] for c in out["check_names"]], [self.it])
        self.assertEqual(out["check_names"][0]["raw_name"], "SPLT TB")

    def test_no_supplier_history_goes_to_ask_about(self):
        out = self.plan()
        self.assertIn(self.it, [a["item_id"] for a in out["ask_about"]])

    def test_discounts_hidden_without_costs_read(self):
        self.buy(self.it, self.s1, public=10000, net=8000)
        ctx = replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"})
        r = {x["item_id"]: x for x in self.plan(ctx=ctx)["rows"]}[self.it]
        self.assertIsNone(r["best"])                                     # not even which one is cheaper
        self.assertTrue(all(s["discount_bp"] is None for s in r["suppliers"]))
        self.assertNotIn("below_official", r["flags"])
        self.assertNotIn("below_last", r["flags"])

    def test_a_stopped_supplier_is_never_the_best(self):
        self.buy(self.it, self.s1, public=10000, net=7000)
        self.buy(self.it, self.s2, public=10000, net=9000)
        with self.db.transaction():
            self.db.execute("UPDATE suppliers SET is_active=0 WHERE id=?", (self.s1,))
        self.assertEqual(self.rows()[self.it]["best"]["supplier_id"], self.s2)


class TestOrders(PlanBase):
    def setUp(self):
        super().setUp()
        self.it = self.item("Track Tab", pack_size=2)
        self.months(self.it, {m: 60 for m in MONTHS12})

    def test_create_and_track_sent_arrived_partial_na(self):
        o = pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}], now=TODAY)
        self.assertEqual(o["status"], "sent")
        self.assertEqual(self.rows()[self.it]["on_order"], 10)              # 5 packs x 2
        pp.set_line(self.db, self.ctx, o["id"], self.it, status="partial", got=3, now=TODAY)
        r = self.rows()[self.it]
        self.assertEqual((r["on_order"], r["arrived"]), (0, 6))
        out = self.plan()
        self.assertIn(self.it, [a["item_id"] for a in out["ask_about"]])     # 2 packs did not come
        pp.set_line(self.db, self.ctx, o["id"], self.it, status="na", now=TODAY)
        self.assertEqual(self.rows()[self.it]["arrived"], 0)
        self.assertEqual(pp.list_orders(self.db, self.ctx)[0]["lines"][0]["status"], "na")

    def test_arrived_stops_counting_once_its_invoice_is_entered(self):
        o = pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}], now=TODAY - timedelta(days=2))
        pp.set_line(self.db, self.ctx, o["id"], self.it, status="arrived", now=TODAY - timedelta(days=1))
        self.assertEqual(self.rows()[self.it]["arrived"], 10)
        self.buy(self.it, self.s1, qty=5, status="draft", days_ago=0)
        self.assertEqual(self.rows()[self.it]["arrived"], 10)               # the draft itself, never both

    def test_a_sent_order_whose_invoice_came_is_not_on_order_twice(self):
        pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}],
                        now=TODAY - timedelta(days=2))
        self.buy(self.it, self.s1, qty=5, status="approved", days_ago=1)
        self.assertEqual(self.rows()[self.it]["on_order"], 0)

    def test_old_sent_lines_stop_counting(self):
        pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}],
                        now=TODAY - timedelta(days=pp.SENT_DAYS + 1))
        self.assertEqual(self.rows()[self.it]["on_order"], 0)

    def test_refusals(self):
        bad = [[], [{"item_id": self.it, "packs": 0}], [{"item_id": self.it, "packs": "1.5"}],
               [{"item_id": self.it, "packs": 2}, {"item_id": self.it, "packs": 3}]]
        for lines in bad:
            with self.assertRaises(ValidationFailed):
                pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=lines, now=TODAY)
        with self.assertRaises(NotFound):
            pp.create_order(self.db, self.ctx, supplier_id="nope", lines=[{"item_id": self.it, "packs": 1}], now=TODAY)
        with self.assertRaises(NotFound):
            pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": "nope", "packs": 1}], now=TODAY)
        o = pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}], now=TODAY)
        for st, got in (("partial", 0), ("partial", 5), ("partial", 9), ("x", None)):
            with self.assertRaises(ValidationFailed):
                pp.set_line(self.db, self.ctx, o["id"], self.it, status=st, got=got, now=TODAY)
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            pp.set_line(self.db, other, o["id"], self.it, status="arrived", now=TODAY)
        ro = replace(self.ctx, permissions=self.ctx.permissions - {"purchases.create"})
        with self.assertRaises(Forbidden):
            pp.create_order(self.db, ro, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 1}], now=TODAY)

    def test_orders_are_audited(self):
        pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}], now=TODAY)
        self.assertTrue(self.db.one("SELECT 1 FROM audit_log WHERE action='purchase_order.create'"))


class TestOutputs(PlanBase):
    def setUp(self):
        super().setUp()
        self.a = self.item("Cheap Tab", public_price=1000)
        self.b = self.item("Dear Tab", public_price=20000)

    def test_supplier_name_mapping_remembered_and_used(self):
        self.buy(self.a, self.s1, raw="CHEAP TB 20")
        names = pp.supplier_names(self.db, self.ctx, self.s1, [self.a, self.b])
        self.assertEqual(names[self.a], "CHEAP TB 20")                       # the supplier's own paper name
        self.assertEqual(names[self.b], "Dear Tab")                          # ours when he never sold it
        pp.remember_name(self.db, self.ctx, self.s1, self.b, "DEAR TABLETS")
        self.assertEqual(pp.supplier_names(self.db, self.ctx, self.s1, [self.b])[self.b], "DEAR TABLETS")
        self.assertEqual(pp.supplier_names(self.db, self.ctx, self.s2, [self.b])[self.b], "Dear Tab")   # per supplier

    def test_excel_item_and_qty_only_dearest_first(self):
        from openpyxl import load_workbook
        data = pp.order_xlsx(self.db, self.ctx, [{"supplier_id": self.s1, "lines": [
            {"item_id": self.a, "packs": 4}, {"item_id": self.b, "packs": 1}]}])
        ws = load_workbook(io.BytesIO(data)).worksheets[0]
        rows = [[c.value for c in r] for r in ws.iter_rows()]
        self.assertEqual(rows[1], ["الصنف", "الكمية (علبة)"])
        self.assertEqual(rows[2:], [["Dear Tab", 1], ["Cheap Tab", 4]])

    def test_message_draft_is_text_only(self):
        txt = pp.message_draft(self.db, self.ctx, self.s1, [{"item_id": self.a, "packs": 4}])
        self.assertIn("Cheap Tab", txt)
        self.assertIn("4", txt)
        self.assertIn("قبل الطباعة", txt)
        self.assertIn("مورد أ", txt)


class TestAPI(APICase):
    def test_endpoints(self):
        self.http_login()
        s = cat.create_supplier(self.db, self.ctx, name="مورد ج")
        r = self.c.get("/api/procurement/plan", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("rows", r.json())
        r = self.c.post("/api/procurement/orders", json={"supplier_id": s, "lines": [{"item_id": self.pan, "packs": 2}]},
                        headers={**H, "Idempotency-Key": new_id()})
        self.assertEqual(r.status_code, 200, r.text)
        oid = r.json()["id"]
        r = self.c.post(f"/api/procurement/orders/{oid}/line", json={"item_id": self.pan, "status": "arrived"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/procurement/orders", headers=H).json()["orders"][0]["lines"][0]["status"],
                         "arrived")
        r = self.c.post("/api/procurement/supplier-name", json={"supplier_id": s, "item_id": self.pan, "name": "PANA"},
                        headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post("/api/procurement/message", json={"supplier_id": s, "lines": [{"item_id": self.pan, "packs": 2}]},
                        headers=H)
        self.assertIn("PANA", r.json()["text"])
        r = self.c.post("/api/procurement/plan.xlsx", json={"groups": [{"supplier_id": s, "lines": [
            {"item_id": self.pan, "packs": 2}]}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)


class TestReviewFixes(PlanBase):
    """Independent review of U4-2: P1 + P2 fixes."""
    def setUp(self):
        super().setUp()
        self.it = self.item("Review Tab", pack_size=2)
        self.months(self.it, {m: 60 for m in MONTHS12})

    def test_marked_arrived_after_the_invoice_is_not_counted_twice(self):
        o = pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}],
                            now=TODAY - timedelta(days=2))
        self.buy(self.it, self.s1, qty=5, status="draft", days_ago=1)          # entered first
        pp.set_line(self.db, self.ctx, o["id"], self.it, status="arrived", now=TODAY)   # ticked after
        self.assertEqual(self.rows()[self.it]["arrived"], 10)               # the draft only

    def test_cheapest_supplier_hidden_without_costs_read(self):
        self.buy(self.it, self.s1, public=10000, net=9000)
        self.buy(self.it, self.s2, public=10000, net=7000)
        ctx = replace(self.ctx, permissions=self.ctx.permissions - {"costs.read"})
        r = {x["item_id"]: x for x in self.plan(ctx=ctx)["rows"]}[self.it]
        self.assertIsNone(r["best"])
        self.assertEqual([s["supplier"] for s in r["suppliers"]], sorted(s["supplier"] for s in r["suppliers"]))

    def test_ask_about_clears_once_bought_elsewhere(self):
        o = pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 5}],
                            now=TODAY - timedelta(days=3))
        pp.set_line(self.db, self.ctx, o["id"], self.it, status="na", now=TODAY - timedelta(days=2))
        self.assertIn(self.it, [a["item_id"] for a in self.plan()["ask_about"] if a["why"] == "na"])
        self.buy(self.it, self.s2, qty=5, days_ago=1)
        self.assertNotIn(self.it, [a["item_id"] for a in self.plan()["ask_about"] if a["why"] == "na"])

    def test_one_branch_never_pushes_out_another(self):
        b2 = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO branches (id, org_id, name) VALUES (?,?,?)", (b2, self.ctx.org_id, "فرع 2"))
        ctx2 = replace(self.ctx, branch_id=b2)
        pp.create_order(self.db, self.ctx, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 1}], now=TODAY)
        with mock.patch.object(pp, "MAX_ORDERS", 3):
            for _ in range(4):
                pp.create_order(self.db, ctx2, supplier_id=self.s1, lines=[{"item_id": self.it, "packs": 1}], now=TODAY)
        self.assertEqual(len(pp.list_orders(self.db, self.ctx)), 1)
        self.assertEqual(len(pp.list_orders(self.db, ctx2)), 3)

    def test_remembered_name_wins_even_if_learned_before(self):
        from app.catalog import service as cs
        pp.remember_name(self.db, self.ctx, self.s1, self.it, "NAME A")
        cs.add_alias(self.db, self.ctx, self.it, "NAME B", supplier_id=self.s1, source="learned")
        pp.remember_name(self.db, self.ctx, self.s1, self.it, "NAME B")
        self.assertEqual(pp.supplier_names(self.db, self.ctx, self.s1, [self.it])[self.it], "NAME B")

    def test_previous_discount_comes_from_an_earlier_invoice(self):
        self.buy(self.it, self.s1, public=10000, net=7500, days_ago=30)            # 25% before
        pid = self.buy(self.it, self.s1, public=10000, net=8000, days_ago=5)       # 20% now
        with self.db.transaction():                                                # same invoice, a 2nd line at 15%
            self.db.execute("INSERT INTO purchase_invoice_lines (id, invoice_id, org_id, line_no, raw_name, item_id, qty,"
                            " bonus_qty, public_price, unit_net_price, match_status) VALUES (?,?,?,2,'x',?,10,0,10000,8500,"
                            "'confirmed')", (new_id(), pid, self.ctx.org_id, self.it))
        r = self.rows()[self.it]
        self.assertEqual(r["best"]["prev_bp"], 2500)
