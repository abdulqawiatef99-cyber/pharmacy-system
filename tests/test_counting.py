from decimal import Decimal

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.inventory import service as inv
from app.inventory.counting import Counting
from tests.helpers import DBTestCase


class CountCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.cnt = Counting(self.db)
        self.a = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "location": "A1"})
        self.b = cat.create_item(self.db, self.ctx, {"name_en": "Cataflam 50mg 20 tab", "location": "B2"})
        self.c = cat.create_item(self.db, self.ctx, {"name_en": "Concor 5mg 30 tab", "location": "A1"})
        with self.db.transaction():
            self.ba1 = inv.get_or_create_batch(self.db, self.ctx, self.a, "OLD", "2027-01-31")
            self.ba2 = inv.get_or_create_batch(self.db, self.ctx, self.a, "NEW", "2028-01-31")
            for item, batch, q in ((self.a, self.ba1, 10), (self.a, self.ba2, 10), (self.b, None, 5),
                                   (self.c, None, 7)):
                inv.post_movement(self.db, self.ctx, item_id=item, qty_delta=q, batch_id=batch,
                                  movement_type="opening", unit_cost=Decimal("2"))
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        self.actx = self.login("asst", "Assist-Pass-2026")


class TestCounting(CountCase):
    def test_full_cycle_with_item_level_shortage_fefo(self):
        sid = self.cnt.create(self.ctx, title="جرد أكتوبر")
        r = self.cnt.record(self.actx, sid, item_id=self.a, qty=17)        # 3 missing
        self.assertNotIn("system_qty", r)                                   # blind
        self.cnt.record(self.actx, sid, item_id=self.b, qty=6)              # 1 extra
        v = self.cnt.variances(self.ctx, sid)
        d = {l["item_id"]: l["difference"] for l in v["lines"]}
        self.assertEqual(d, {self.a: -3, self.b: 1})
        self.assertEqual([u["item_id"] for u in v["uncounted_with_stock"]], [self.c])
        rep = self.cnt.approve(self.ctx, sid)
        self.assertEqual(rep["adjustment_lines"], 2)
        s = inv.stock_for_item(self.db, self.ctx, self.a)
        self.assertEqual({b["batch_no"]: b["qty"] for b in s["batches"]}, {"OLD": 7, "NEW": 10})
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.c)["total_qty"], 7)  # untouched
        self.assertEqual(inv.verify_balances(self.db, self.ctx.org_id), [])
        with self.assertRaises(Conflict):
            self.cnt.record(self.actx, sid, item_id=self.a, qty=1)

    def test_no_silent_double_count(self):
        sid = self.cnt.create(self.ctx, title="x")
        self.cnt.record(self.actx, sid, item_id=self.b, qty=5)
        with self.assertRaises(Conflict) as e:
            self.cnt.record(self.ctx, sid, item_id=self.b, qty=4)
        self.assertEqual(e.exception.code, "already_counted")
        self.cnt.record(self.ctx, sid, item_id=self.b, qty=4, recount=True)
        v = self.cnt.variances(self.ctx, sid)
        self.assertEqual(v["lines"][0]["counted"], 4)
        n = self.db.one("SELECT count(*) FROM count_entries WHERE session_id=?", (sid,))[0]
        self.assertEqual(n, 2)                                   # history kept

    def test_scanning_mode_adds_up(self):
        sid = self.cnt.create(self.ctx, title="x")
        for k in range(3):
            # QA-29: the same +1 within 2 s is asked first (a double read); here each one is confirmed as real
            self.cnt.record(self.actx, sid, item_id=self.b, qty=1, mode="add", confirm_repeat=k > 0)
        self.assertEqual(self.cnt.variances(self.ctx, sid)["lines"][0]["counted"], 3)

    def test_sales_during_count_are_accounted(self):
        sid = self.cnt.create(self.ctx, title="x")
        self.cnt.record(self.actx, sid, item_id=self.b, qty=5)          # matches system
        with self.db.transaction():                                      # 2 sold after counting
            inv.post_movement(self.db, self.ctx, item_id=self.b, qty_delta=-2, movement_type="sale")
        v = self.cnt.variances(self.ctx, sid)
        self.assertEqual(v["lines"][0]["difference"], 0)
        self.cnt.approve(self.ctx, sid)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.b)["total_qty"], 3)

    def test_location_scope(self):
        sid = self.cnt.create(self.ctx, title="رف A1", scope="location", locations=["A1"])
        with self.assertRaises(ValidationFailed):
            self.cnt.record(self.actx, sid, item_id=self.b, qty=1)       # B2 out of scope
        self.cnt.record(self.actx, sid, item_id=self.a, qty=20)
        v = self.cnt.variances(self.ctx, sid)
        self.assertEqual([u["item_id"] for u in v["uncounted_with_stock"]], [self.c])

    def test_zero_uncounted_needs_reason(self):
        sid = self.cnt.create(self.ctx, title="x", scope="items", item_ids=[self.c])
        with self.assertRaises(ValidationFailed):
            self.cnt.approve(self.ctx, sid, zero_uncounted=True)
        preview = self.cnt.variances(self.ctx, sid)
        self.cnt.approve(self.ctx, sid, zero_uncounted=True, reason="الصنف مش موجود على الرف نهائيًا",
                         uncounted_item_ids=[u["item_id"] for u in preview["uncounted_with_stock"]],
                         uncounted_confirmation=preview["uncounted_confirmation"])
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.c)["total_qty"], 0)

    def test_mixed_levels_blocks(self):
        sid = self.cnt.create(self.ctx, title="x")
        self.cnt.record(self.ctx, sid, item_id=self.a, qty=10, batch_id=self.ba1)
        self.cnt.record(self.ctx, sid, item_id=self.a, qty=10)
        with self.assertRaises(Conflict) as e:
            self.cnt.approve(self.ctx, sid)
        self.assertEqual(e.exception.code, "count_problems")

    def test_permissions(self):
        with self.assertRaises(Forbidden):
            self.cnt.create(self.actx, title="x")            # assistant can count, not open/approve
        sid = self.cnt.create(self.ctx, title="x")
        with self.assertRaises(Forbidden):
            self.cnt.approve(self.actx, sid)


class TestCountingEdges(CountCase):
    def test_a_cancelled_count_takes_no_entries_and_moves_no_stock(self):
        sid = self.cnt.create(self.ctx, title="x")
        self.cnt.record(self.actx, sid, item_id=self.b, qty=1)
        self.cnt.cancel(self.ctx, sid)
        with self.assertRaises(Conflict):
            self.cnt.record(self.actx, sid, item_id=self.b, qty=2)
        with self.assertRaises(Conflict):
            self.cnt.approve(self.ctx, sid)
        with self.assertRaises(Conflict):
            self.cnt.cancel(self.ctx, sid)
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.b)["total_qty"], 5)
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action='count.cancel'")["n"], 1)
        with self.assertRaises(Forbidden):
            self.cnt.cancel(self.actx, self.cnt.create(self.ctx, title="y"))

    def test_an_open_count_shows_the_system_quantity_to_who_may_see_stock(self):
        sid = self.cnt.create(self.ctx, title="x", blind=False)
        r = self.cnt.record(self.actx, sid, item_id=self.b, qty=4)
        self.assertEqual(r["system_qty"], 5)

    def test_a_count_is_refused_when_it_cannot_be_right(self):
        with self.assertRaises(ValidationFailed):
            self.cnt.create(self.ctx, title="x", scope="shelf")
        with self.assertRaises(ValidationFailed):
            self.cnt.create(self.ctx, title="x", scope="location", locations=[])
        with self.assertRaises(ValidationFailed):
            self.cnt.create(self.ctx, title="x", scope="items", item_ids=[])
        with self.assertRaises(NotFound):
            self.cnt.create(self.ctx, title="x", scope="items", item_ids=["missing"])
        self.cnt.create(self.ctx, title="full")
        with self.assertRaises(Conflict):
            self.cnt.create(self.ctx, title="another full")                  # one full count at a time
        self.cnt.create(self.ctx, title="a shelf meanwhile", scope="location", locations=["A1"])

    def test_bad_entries_are_refused_and_record_nothing(self):
        sid = self.cnt.create(self.ctx, title="x")
        for kw, exc in (({"qty": -1}, ValidationFailed), ({"qty": 1.5}, ValidationFailed), ({"qty": "3"}, ValidationFailed),
                        ({"qty": 1, "mode": "replace"}, ValidationFailed), ({"qty": 1, "item_id": "missing"}, NotFound),
                        ({"qty": 1, "batch_id": self.ba1}, NotFound)):                # a batch of another item
            with self.subTest(kw=kw):
                with self.assertRaises(exc):
                    self.cnt.record(self.actx, sid, **{"item_id": self.b, **kw})
        self.assertEqual(self.db.one("SELECT count(*) n FROM count_entries WHERE session_id=?", (sid,))["n"], 0)

    def test_selling_more_after_the_count_than_was_counted_blocks_the_approval(self):
        sid = self.cnt.create(self.ctx, title="x")
        self.cnt.record(self.actx, sid, item_id=self.b, qty=1)               # 1 counted, 5 in the system
        with self.db.transaction():                                          # then 3 sold
            inv.post_movement(self.db, self.ctx, item_id=self.b, qty_delta=-3, movement_type="sale")
        v = self.cnt.variances(self.ctx, sid)
        self.assertEqual(v["lines"][0]["problem"], "negative_after_sales")
        with self.assertRaises(Conflict) as e:
            self.cnt.approve(self.ctx, sid)
        self.assertEqual(e.exception.code, "count_problems")
        self.assertEqual(inv.stock_for_item(self.db, self.ctx, self.b)["total_qty"], 2)    # untouched
