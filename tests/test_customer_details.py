"""Customer: second name, addresses, three mobiles, WhatsApp click-to-chat reminder."""
from urllib.parse import unquote

from app.core.errors import Conflict, ValidationFailed
from app.sales import customers as cu
from tests.test_cashier_acceptance import Base


class TestCustomerDetails(Base):
    def test_fields_search_phones_and_reminder(self):
        cid = self.s.create_customer(self.ctx, name="أحمد محمد", phone="٠١٠٠١٢٣٤٥٦٧", name_alt="منى (مراته)",
                                     address1="شارع سعد زغلول", phone2="01223334444")
        p = cu.profile(self.db, self.ctx, cid)
        self.assertEqual((p["phone"], p["phone2"], p["address1"]), ("01001234567", "01223334444", "شارع سعد زغلول"))
        self.assertEqual(p["wa"], ["201001234567", "201223334444"])
        for q in ("منى", "01223", "أحمد"):
            self.assertEqual([c["id"] for c in cu.listing(self.db, self.ctx, q)], [cid], q)
        with self.assertRaises(Conflict):                       # someone else's second mobile
            self.s.create_customer(self.ctx, name="تاني", phone="01223334444")
        with self.assertRaises(ValidationFailed):
            cu.update(self.db, self.ctx, cid, {"phone3": "12ab"})
        cu.update(self.db, self.ctx, cid, {"phone3": "01112223333", "address2": "البيت التاني"})
        with self.assertRaises(Conflict):                       # not agreed to messages yet
            cu.whatsapp_reminder(self.db, self.ctx, cid)
        cu.update(self.db, self.ctx, cid, {"whatsapp_ok": True})
        cu.save_refill(self.db, self.ctx, cid, self.pan, 2)
        r = cu.whatsapp_reminder(self.db, self.ctx, cid)
        self.assertTrue(r["url"].startswith("https://wa.me/201001234567?text="))
        self.assertIn("أحمد", unquote(r["url"]))
        self.assertNotIn("Panadol", r["text"])                  # medicine names never leave
        self.assertTrue(cu.profile(self.db, self.ctx, cid)["reminded_at"])

    def test_wa_number(self):
        self.assertEqual(cu.wa_number("01001234567"), "201001234567")
        self.assertEqual(cu.wa_number("+966501234567"), "966501234567")
        self.assertIsNone(cu.wa_number("0482221234"))           # landline


class TestLineOrder(Base):
    def test_alpha_default_and_entry(self):
        from app.catalog import service as cat
        from app.inventory import service as inv
        from app.sales.service import sort_lines
        from decimal import Decimal
        z = cat.create_item(self.db, self.ctx, {"name_en": "Zyrtec 10mg", "public_price": 3000})
        a = cat.create_item(self.db, self.ctx, {"name_en": "augmentin 1g", "public_price": 9000})
        with self.db.transaction():
            for i in (z, a):
                b = inv.get_or_create_batch(self.db, self.ctx, i, "X", "2030-01-31")
                inv.post_movement(self.db, self.ctx, item_id=i, qty_delta=5, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("10"))
        sid = self.s.create(self.ctx)
        for i in (z, self.pan, a):
            self.s.add_line(self.ctx, sid, i, packs=1)
        names = [l["item"]["name_en"] for l in self.s.get(self.ctx, sid)["lines"]]
        self.assertEqual(names, ["augmentin 1g", "Panadol 500mg 24 tab", "Zyrtec 10mg"])     # A→Z, case ignored
        with self.db.transaction():
            self.db.execute("INSERT INTO org_settings (org_id, key, value) VALUES (?, 'line_order', 'entry')", (self.ctx.org_id,))
        s = self.s.get(self.ctx, sid)
        self.assertEqual([l["line_no"] for l in s["lines"]], [1, 2, 3])
        self.assertEqual(s["line_order"], "entry")
        self.assertEqual(sort_lines([], "alpha"), [])
