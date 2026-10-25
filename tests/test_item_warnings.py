"""Item warnings at the cashier: must be confirmed, logged with the pharmacist's name, reported."""
from app.catalog import service as cat
from app.core.errors import Conflict
from app.sales.service import local_today, warnings_report
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base


class TestWarnings(Base):
    def test_confirm_required_and_logged(self):
        cat.update_item(self.db, self.ctx, self.pan, {"rx_required": True, "misuse_risk": 1, "sale_note": "اسأل عن الحمل"})
        self.cd.open(self.ctx, 0)
        sid = self.s.create(self.ctx)
        d = self.s.add_line(self.ctx, sid, self.pan, units=2)
        self.assertTrue(d["lines"][0]["item"]["rx_required"])
        f = self.s.safety_check(self.ctx, sid)
        codes = [x.code for x in f]
        for c in ("item_rx", "item_misuse", "item_note"):
            self.assertIn(c, codes)
        with self.assertRaises(Conflict) as e:                       # not confirmed: no sale
            self.s.complete(self.ctx, sid, paid_cash=d["total"])
        self.assertEqual(e.exception.code, "safety_unresolved")
        self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions={i: "روشتة د. أحمد" for i in range(len(f))})
        t = local_today().isoformat()
        r = warnings_report(self.db, self.ctx, t, t)
        self.assertEqual(r["counts"], {"item_rx": 1, "item_misuse": 1, "item_note": 1})
        self.assertIn("روشتة د. أحمد", [x["reason"] for x in r["rows"]])
        cat.update_item(self.db, self.ctx, self.pan, {"rx_required": False, "misuse_risk": False, "sale_note": ""})
        sid2 = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid2, self.pan, units=2)
        self.assertFalse([x for x in self.s.safety_check(self.ctx, sid2) if x.code.startswith("item_")])


class TestFlagsAPI(APICase):
    def test_bulk_flags(self):
        self.http_login()
        r = self.c.post("/api/items/flags", json={"item_ids": [self.pan], "rx_required": True}, headers=H)
        self.assertEqual(r.json()["updated"], 1)
        self.assertEqual(self.db.one("SELECT rx_required FROM items WHERE id=?", (self.pan,))["rx_required"], 1)
