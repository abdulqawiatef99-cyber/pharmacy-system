"""Transfer proof with the sale invoice, review, duplicate screenshot, transfers report."""
from app.core import notify, people
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.sales import attachments as att
from app.sales.service import local_today
from tests.test_api import APICase, H
from tests.test_cashier_acceptance import Base

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
PW = "Staff-Pass-2026"


class TestTransferProof(Base):
    def test_attach_review_duplicate_and_report(self):
        people.add_user(self.db, self.ctx, username="asst", full_name="A", password=PW, role_code="assistant")
        a = self.login("asst", PW)
        self.cd.open(self.ctx, 0)  # every store sale needs an open shift (final review P2-2)
        s1 = self.sell(ctx=a, cash=0, card=4000)
        with self.db.transaction():                                  # make it a transfer sale
            self.db.execute("UPDATE sales SET paid_card=0, paid_transfer=4000 WHERE id=?", (s1["id"],))
        r = att.attach(self.db, self.tmp, a, s1["id"], PNG, "shot.png")
        self.assertEqual(len(r["attachments"]), 1)
        with self.assertRaises(ValidationFailed):
            att.attach(self.db, self.tmp, a, s1["id"], b"MZ not an image")
        with self.assertRaises(Conflict):
            att.attach(self.db, self.tmp, a, s1["id"], PNG)
        data, mime = att.file(self.db, self.tmp, a, r["attachments"][0]["id"])
        self.assertEqual((data, mime), (PNG, "image/png"))
        s2 = self.sell(ctx=a, cash=0, card=4000)
        r2 = att.attach(self.db, self.tmp, a, s2["id"], PNG)        # same screenshot, another invoice
        self.assertEqual(r2["duplicate_of"], s1["sale_no"])
        self.assertIn("transfer_duplicate", [n["kind"] for n in notify.listing(self.db, self.ctx)["items"]])
        aid = r["attachments"][0]["id"]
        with self.assertRaises(Forbidden):
            att.review(self.db, a, aid, True)
        with self.assertRaises(ValidationFailed):
            att.review(self.db, self.ctx, aid, False)                # a problem needs a note
        att.review(self.db, self.ctx, aid, False, "الفلوس موصلتش")
        t = att.transfers(self.db, self.ctx, local_today().isoformat(), local_today().isoformat())
        self.assertEqual([x["state"] for x in t["rows"]], ["problem"])
        self.assertEqual(t["totals"]["problem"], 4000)
        self.assertEqual(len(self.s.get(self.ctx, s1["id"])["attachments"]), 1)


class TestTransferProofAPI(APICase):
    def test_upload_and_get(self):
        self.http_login()
        sid = self.c.post("/api/sales", json={}, headers=H).json()["id"]
        r = self.c.post(f"/api/sales/{sid}/attachments", files={"file": ("t.png", PNG, "image/png")}, data={"note": "إنستاباي"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        aid = r.json()["attachments"][0]["id"]
        g = self.c.get(f"/api/attachments/{aid}")
        self.assertEqual((g.status_code, g.headers["content-type"]), (200, "image/png"))
        self.assertEqual(self.c.post(f"/api/attachments/{aid}/review", json={"ok": True}, headers=H).json()["review"], "ok")


class TestReturnReceipt(Base):
    def test_return_slip_lines(self):
        self.cd.open(self.ctx, 0)
        sale = self.sell(units=2)
        r = self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1}, refund_method="cash", reason="مش مناسب")
        x = self.s.return_receipt(self.ctx, r["return_id"])
        self.assertEqual([l["returned_now"] for l in x["lines"]], [1])
        self.assertEqual(x["return"]["total"], r["refund"])
        self.assertEqual(len(self.s.get(self.ctx, sale["id"])["returns"]), 1)
