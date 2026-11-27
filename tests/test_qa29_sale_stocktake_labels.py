"""QA-29 PR3 (independent tester, synthetic data, develop 95416e7).

8. After a successful sale, a printer / drawer problem showed as a RED notice that read like a failed sale: now
   «✓ اتحفظ البيع» first, in a yellow note (tests/test_pos_printer_off_toast.py).
9. The first-run tour over the sale search: fixed in PR2 #499 (the same cause as finding 4).
10. Stocktake «إضافة»: the same +3 sent twice (double click, scanner double read) silently became 6. Now the same
    person adding the same amount to the same line within 2 s is asked first («repeat_add»); the screen confirms.
11. Label print with an Arabic item code and no barcode failed silently: the label page answered 500 inside the
    hidden print frame (Code 128 is ASCII only). Now a clear Arabic message, before anything prints, on both paths.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.catalog import service as cat
from app.core.errors import Conflict
from app.inventory.counting import Counting
from app.printing import settings as rs
from tests.test_api import APICase, H

APP_JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")


class TestRepeatAdd(APICase):
    def setUp(self):
        super().setUp()
        self.k = Counting(self.db)

    def session(self):
        return self.k.create(self.ctx, title="QA29 count", scope="items", item_ids=[self.pan], blind=False)

    def test_the_same_add_twice_within_two_seconds_is_asked_first(self):
        sid = self.session()
        self.assertEqual(self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="add")["qty"], 3)
        with self.assertRaises(Conflict) as e:
            self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="add")
        self.assertEqual(e.exception.code, "repeat_add")
        self.assertEqual(e.exception.details["qty"], 3)
        live = self.db.one("SELECT qty FROM count_entries WHERE session_id=? AND superseded_by IS NULL", (sid,))
        self.assertEqual(live["qty"], 3, "a refused repeat changed the count")
        self.assertEqual(self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="add", confirm_repeat=True)["qty"], 6)

    def test_another_amount_or_a_later_add_goes_through(self):
        sid = self.session()
        self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="add")
        self.assertEqual(self.k.record(self.ctx, sid, item_id=self.pan, qty=2, mode="add")["qty"], 5)
        old = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self.db.transaction():
            self.db.execute("UPDATE count_entries SET counted_at=? WHERE session_id=?", (old, sid))
        self.assertEqual(self.k.record(self.ctx, sid, item_id=self.pan, qty=2, mode="add")["qty"], 7)

    def test_set_mode_is_not_affected(self):
        sid = self.session()
        self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="set")
        self.assertEqual(self.k.record(self.ctx, sid, item_id=self.pan, qty=3, mode="set")["qty"], 3)

    def test_api_passes_the_confirmation(self):
        sid = self.session()
        self.http_login()
        url = f"/api/counts/{sid}/entries"
        body = {"item_id": self.pan, "qty": 3, "mode": "add"}
        self.assertEqual(self.c.post(url, headers=H, json=body).status_code, 200)
        again = self.c.post(url, headers=H, json=body)
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(again.json()["error"]["code"], "repeat_add")
        ok = self.c.post(url, headers=H, json={**body, "confirm_repeat": True})
        self.assertEqual(ok.json()["qty"], 6)

    def test_screen_asks_and_shows_the_running_total(self):
        part = APP_JS[APP_JS.index("const save = async (recount"):][:1800]
        self.assertTrue('"repeat_add"' in part, "the screen handles the repeat question")
        self.assertTrue("confirm_repeat" in part)


class TestArabicCodeLabels(APICase):
    def setUp(self):
        super().setUp()
        self.bad = cat.create_item(self.db, self.ctx, {"name_ar": "صنف تجريبي", "code": "كود١٢", "pack_size": 1,
                                                       "public_price": 1000})
        self.http_login()

    def audits(self):
        return self.db.one("SELECT count(*) n FROM audit_log WHERE org_id=? AND action='print.labels'",
                           (self.ctx.org_id,))["n"]

    def test_browser_labels_say_why_instead_of_an_internal_error(self):
        r = self.c.get(f"/labels?items={self.bad}:1", headers=H)
        self.assertEqual(r.status_code, 422, r.text[:300])
        err = r.json()["error"]
        self.assertEqual(err["code"], "label_barcode")
        self.assertIn("صنف تجريبي", err["message"])
        self.assertIn("باركود", err["message"])

    def test_check_answers_before_printing_and_records_nothing(self):
        bad = self.c.get(f"/labels?items={self.bad}:1&check=1", headers=H)
        self.assertEqual(bad.status_code, 422)
        cat.add_barcode(self.db, self.ctx, self.pan, "6221043010359")
        good = self.c.get(f"/labels?items={self.pan}:1&check=1", headers=H)
        self.assertEqual(good.status_code, 204, good.text[:200])
        self.assertEqual(self.audits(), 0)

    def test_label_printer_path_says_it_too(self):
        rs.save(self.db, self.ctx, {"label_lang": "tspl", "label_target": "tcp:127.0.0.1:9100"})   # never reached
        r = self.c.post("/api/print/labels", headers=H, json={"items": [{"id": self.bad, "count": 1}]})
        self.assertEqual(r.status_code, 422, r.text[:300])
        self.assertEqual(r.json()["error"]["code"], "label_barcode")

    def test_a_label_printer_set_on_this_device_is_checked_too(self):
        # review of QA-31 PR3: the org says browser, this device has its own label printer, so it prints direct
        rs.save(self.db, self.ctx, {"label_lang": "browser"})
        with self.db.transaction():
            self.db.execute("UPDATE registers SET label_printer='tcp:127.0.0.1:9100' WHERE org_id=?", (self.ctx.org_id,))
        r = self.c.post("/api/print/labels", headers=H, json={"items": [{"id": self.bad, "count": 1}]})
        self.assertEqual(r.status_code, 422, r.text[:300])
        self.assertEqual(r.json()["error"]["code"], "label_barcode")

    def test_the_printer_check_needs_items_read_first(self):
        from app.core.permissions import Context
        from app.core.errors import Forbidden
        rs.save(self.db, self.ctx, {"label_lang": "tspl", "label_target": "tcp:127.0.0.1:9100"})
        nobody = Context(user_id=self.ctx.user_id, org_id=self.ctx.org_id, branch_id=self.ctx.branch_id,
                         permissions=frozenset())
        from app.printing import service as ps
        with self.assertRaises(Forbidden):
            ps.print_labels(self.db, nobody, [(self.bad, 1)])
        src = (Path(__file__).resolve().parent.parent / "app" / "api" / "app.py").read_text(encoding="utf-8")
        run = src[src.index("async def print_labels_api"):][:1200]
        self.assertLess(run.index('ctx.require("items.read")'), run.index("_label_barcode_ok"))

    def test_without_the_barcode_field_the_label_prints(self):
        rs.save(self.db, self.ctx, {"label_fields": {"barcode": False}})
        r = self.c.get(f"/labels?items={self.bad}:1", headers=H)
        self.assertEqual(r.status_code, 200, r.text[:300])

    def test_screen_checks_before_the_print_frame(self):
        part = APP_JS[APP_JS.index("async function printLabels(list)"):][:1500]
        self.assertTrue("check=1" in part, "the browser path asks first, so a refusal is shown, not swallowed")
