"""Synthetic regressions for the three FIX-C findings in #461."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

import manage
import release_bootstrap as releases
from app.catalog import service as catalog
from app.core import audit
from app.db import Database
from app.inventory import service as inventory
from app.reports import hikma_claim
from app.sales import rounding
from app.sales.cash import CashDrawer
from app.sales.service import local_day_bounds
from tests import test_u3_3_hikma_claim
from tests.test_api import H
from tests.test_owner_plan_priority_1_2 import PlanCase


def yesterday(day):
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def during(day):
    return local_day_bounds(date.fromisoformat(day))[0]


class TestReissuedHikmaClaim(test_u3_3_hikma_claim.TestHikmaClaim):
    def test_next_period_return_leaves_the_first_claim_intact(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        sale = self.sell(1)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sale["id"]))
        first = hikma_claim.claim(self.db, self.ctx, "shibin", old, old)
        ret = self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 1},
                                  refund_method="credit", reason="اختبار")
        self.assertEqual(ret["refund"], 1900)
        second = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)
        self.assertEqual((first["due"], second["due"]), (1738, -1738))
        self.assertEqual(hikma_claim.claim(self.db, self.ctx, "shibin", old, old), first)

    def test_mixed_lots_and_owners_with_later_partial_returns(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        sale = self.sell(4, disc=1)
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=? AND batch_no='NEW'", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sale["id"]))
        first = hikma_claim.claim(self.db, self.ctx, "shibin", old, old)
        self.assertEqual(first["pharmacy"]["value"] + first["hospital"]["value"], 7699)
        lid = sale["lines"][0]["id"]
        self.s.return_items(self.ctx, sale["id"], {lid: 1}, refund_method="credit", reason="اختبار")
        self.s.return_items(self.ctx, sale["id"], {lid: 1}, refund_method="credit", reason="اختبار")
        second = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)["previous_returns"]
        self.assertEqual(second["value"], 3899)
        self.assertTrue(all(second["by_owner"].values()))
        self.assertEqual(hikma_claim.claim(self.db, self.ctx, "shibin", old, old), first)

    def test_mixed_price_returns_reverse_the_amount_due_for_the_same_lots(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        sale = self.sell(4)
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=? AND batch_no='NEW'", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sale["id"]))
        issued = hikma_claim.claim(self.db, self.ctx, "shibin", old, old)
        lid = sale["lines"][0]["id"]
        for qty in (1, 3):
            self.s.return_items(self.ctx, sale["id"], {lid: qty}, refund_method="credit", reason="اختبار")
            later = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)
            combined = hikma_claim.claim(self.db, self.ctx, "shibin", old, new)
            with self.subTest(qty=qty):
                self.assertEqual(issued["due"] + later["due"], combined["due"])
        self.assertEqual(later["previous_returns"]["by_owner"], {"pharmacy": 5700, "hospital": 2000})
        self.assertEqual(combined["due"], 0)

    def test_cross_owner_discount_reverses_the_same_piastre_for_each_owner(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        sale = self.sell(4, disc=1)
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=? AND batch_no='NEW'", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sale["id"]))
        first = hikma_claim.claim(self.db, self.ctx, "shibin", old, old)
        lid = sale["lines"][0]["id"]
        for _ in range(2):
            self.s.return_items(self.ctx, sale["id"], {lid: 2}, refund_method="credit", reason="اختبار")
        returned = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)["previous_returns"]
        self.assertEqual(returned["by_owner"], {owner: first[owner]["value"] for owner in ("pharmacy", "hospital")})

    def test_mixed_owner_partial_return_reverses_rounded_due(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        sale = self.sell(4, disc=1)
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE item_id=? AND batch_no='NEW'", (self.pan,))
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sale["id"]))
        first = hikma_claim.claim(self.db, self.ctx, "shibin", old, old)
        self.s.return_items(self.ctx, sale["id"], {sale["lines"][0]["id"]: 3}, refund_method="credit", reason="اختبار")
        later = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)
        self.assertEqual(first["due"] + later["due"], 1738)

    def test_legacy_return_uses_actual_discount_by_owner_where_reconcilable(self):
        old, new = yesterday(self.s.today.isoformat()), self.s.today.isoformat()
        item = catalog.create_item(self.db, self.ctx, {"name_en": "Hospital Test Item", "public_price": 2000})
        with self.db.transaction():
            expiry = (self.s.today + timedelta(days=100)).isoformat()
            lot = inventory.get_or_create_batch(self.db, self.ctx, item, "TEST", expiry)
            inventory.post_movement(self.db, self.ctx, item_id=item, batch_id=lot, qty_delta=2,
                                    movement_type="opening", unit_cost=Decimal(10))
        sid = self.s.create(self.ctx, self.cust)
        self.s.add_line(self.ctx, sid, self.pan, units=2, discount_bp=1000)
        draft = self.s.add_line(self.ctx, sid, item, units=2)
        sale = self.s.complete(self.ctx, sid, on_credit=draft["total"], safety_decisions=self.ack_all(sid))
        with self.db.transaction():
            self.db.execute("UPDATE batches SET owner='hospital' WHERE id=?", (lot,))
            self.db.execute("UPDATE sales SET completed_at=? WHERE id=?", (during(old), sid))

        record = audit.record

        def old_audit(db, ctx, action, *args, **kwargs):
            if action == "sale.return":
                kwargs["after"] = {k: v for k, v in kwargs["after"].items() if k != "line_refunds"}
            return record(db, ctx, action, *args, **kwargs)

        with patch.object(audit, "record", side_effect=old_audit):
            self.s.return_items(self.ctx, sid, {l["id"]: 1 for l in sale["lines"]},
                                refund_method="credit", reason="اختبار")
        returned = hikma_claim.claim(self.db, self.ctx, "shibin", new, new)["previous_returns"]
        self.assertEqual(returned["by_owner"], {"pharmacy": 1710, "hospital": 2000})


class TestRefundDate(PlanCase):
    def test_return_only_day_reconciles_and_prior_report_is_stable(self):
        old, new = yesterday(self.today), self.today
        drawer = CashDrawer(self.db)
        shift = drawer.open(self.ctx, 10000)
        rounding.set_step(self.db, self.ctx, 50)
        sid = self.draft()
        d = self.line(sid, self.pan, units=1)
        d = self.call("PATCH", f"/api/sales/{sid}/lines/{d['lines'][0]['id']}", json={"discount_amount": 10})
        self.assertEqual(d["total"], 1990)
        cash = self.complete(sid, [{"method": "cash", "amount": 2000}])
        card = self.sell(self.pan, payments=[{"method": "card", "amount": 2000, "channel_id": self.channel("card")}], units=1)
        drawer.close(self.ctx, counted_cash=12000, float_left=12000)
        with self.db.transaction():
            self.db.execute("UPDATE sales SET completed_at=? WHERE id IN (?,?)", (during(old), cash["id"], card["id"]))
            self.db.execute("UPDATE cash_shifts SET opened_at=?, closed_at=? WHERE id=?", (during(old), during(old), shift))
        summary = lambda day: self.call("GET", f"/api/sales/summary?day={day}")
        report = lambda day: self.call("GET", f"/api/reports/sales?from={day}&to={day}")
        before = summary(old), report(old)
        drawer.open(self.ctx, 12000)
        for sale, method in ((cash, "cash"), (card, "card")):
            self.call("POST", f"/api/sales/{sale['id']}/return", json={
                "lines": {sale["lines"][0]["id"]: 1}, "refund_method": method, "reason": "اختبار",
            })
        self.assertEqual(drawer.summary(self.ctx)["cash_by_kind"]["refund"], -2000)
        after, sales = summary(new), report(new)
        with self.subTest(report="day summary"):
            self.assertEqual((after["total"], after["returns"]), (0, 3990))
            self.assertEqual((after["refund_total"], after["rounding"], after["gross_profit_egp"]), (4000, -10, "-9.90"))
            self.assertEqual(summary(old), before[0])
        with self.subTest(report="sales report"):
            self.assertEqual((sales["totals"]["total"], sales["totals"]["returns"], sales["totals"]["rounding"]),
                             (0, 3990, -10))
            self.assertEqual((sales["daily"][0]["day"], sales["daily"][0]["returns"]), (new, 3990))
            self.assertEqual(report(old), before[1])


class TestPendingDatabaseUpdate(PlanCase):
    def candidate(self):
        sale = self.draft()
        draft = self.line(sale, self.pan, units=1)
        s = replace(self.s, db_path=self.tmp / "cashier.db")
        self.db.backup_to(s.db_path)
        candidate_path = self.tmp / "candidate.db"
        self.db.backup_to(candidate_path)
        previous_path = self.tmp / "previous.db"
        candidate = Database(candidate_path)
        with candidate.transaction():
            candidate.execute("CREATE TABLE pending_candidate_probe (id INTEGER)")
        candidate.close()
        home = releases.code_home(s.data_dir)
        release_id = "a" * 64
        releases.atomic_json(home / "active-release.json", {
            "protocol": releases.PROTOCOL, "active": {"id": release_id, "needs_db": True}, "pending": True,
        })
        releases.write_journal(home, {"state": "ready", "release": release_id, "swaps": [{
            "live": str(s.db_path), "candidate": str(candidate_path), "previous": str(previous_path),
            "live_sha256": releases.file_sha256(s.db_path), "candidate_sha256": releases.file_sha256(candidate_path),
        }]})
        self.assertEqual(releases.resume_journal(home), "swapped")
        db = Database(s.db_path)
        self.addCleanup(db.close)

        with patch.object(manage, "_db", return_value=db), patch("uvicorn.run") as run:
            manage.cmd_serve(SimpleNamespace(host="127.0.0.1", port=8000), s)
        client = TestClient(run.call_args.args[0], cookies=self.c.cookies)
        self.addCleanup(client.close)
        checkout = {
            "payments": [{"method": "cash", "amount": 2000}], "version": draft["version"],
            "request_id": "pending-checkout", "receipt": True, "safety_decisions": {"0": "تمت المراجعة"},
        }
        return home, release_id, db, client, sale, checkout

    def test_cashier_refused_during_candidate_health_check(self):
        home, release_id, db, client, sale, checkout = self.candidate()
        with patch("app.printing.service.checkout_devices", return_value={"receipt": "printed"}) as devices:
            self.assertTrue(client.get("/api/health").json()["ok"])
            opening = client.post("/api/cash/open", headers=H, json={"opening_cash": 10000})
            closing_sale = client.post(f"/api/sales/{sale}/complete", headers=H, json=checkout)
            with self.subTest(operation="no cashier writes"):
                self.assertEqual((opening.status_code, closing_sale.status_code), (503, 503), closing_sale.text)
                self.assertEqual(opening.json()["error"]["code"], "update_pending")
                self.assertEqual(db.one("SELECT status FROM sales WHERE id=?", (sale,))["status"], "draft")
                self.assertIsNone(db.one("SELECT 1 FROM cash_shifts"))
                devices.assert_not_called()
            db.close()
            releases.recover_failed_start(home, release_id)
            self.assertEqual(releases.read_journal(home)["state"], "rolled_back")
            self.assertIsNone(db.one("SELECT 1 FROM cash_shifts"))
            self.assertEqual(db.one("SELECT status FROM sales WHERE id=?", (sale,))["status"], "draft")
            self.assertEqual(client.get(f"/receipt/{sale}").status_code, 503)

    def test_checkout_can_retry_after_same_candidate_is_committed(self):
        home, release_id, db, client, sale, checkout = self.candidate()
        headers = {**H, "Idempotency-Key": "first-checkout"}
        with patch("app.printing.service.checkout_devices", return_value={"receipt": "printed"}) as devices:
            self.assertEqual(client.post(f"/api/sales/{sale}/complete", headers=headers, json=checkout).status_code, 503)
            releases.confirm(home, release_id)
            opening = {"opening_cash": 10000}
            self.assertEqual(client.post("/api/cash/open", headers=H, json=opening).status_code, 503)
            releases.finish_journal(home, release_id)
            self.assertEqual(client.post("/api/cash/open", headers=H, json=opening).status_code, 201)
            done = client.post(f"/api/sales/{sale}/complete", headers=headers, json=checkout)
            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual(done.json()["status"], "completed")
            self.assertEqual(db.one("SELECT status FROM sales WHERE id=?", (sale,))["status"], "completed")
            devices.assert_called_once()
        self.assertEqual(releases.resume_journal(home), "done")
        self.assertEqual(db.one("SELECT status FROM sales WHERE id=?", (sale,))["status"], "completed")

    def test_shortcut_retries_the_page_while_candidate_is_pending(self):
        _, _, _, client, _, _ = self.candidate()
        self.assertTrue(client.get("/api/health").json()["ok"])
        waiting = client.get("/")
        self.assertEqual(waiting.status_code, 503)
        self.assertIn("text/html", waiting.headers["content-type"])
        self.assertIn('http-equiv="refresh"', waiting.text)

    def test_waiting_requests_leave_launcher_metadata_free_to_replace(self):
        _, _, _, client, _, _ = self.candidate()
        self.assertTrue(client.get("/api/health").json()["ok"])
        # An open destination can prevent os.replace on Windows. During the wait the
        # candidate must leave both replaceable files to the installed launcher.
        with patch.object(releases, "read_journal", wraps=releases.read_journal) as journal:
            with patch.object(releases, "read_state", wraps=releases.read_state) as state:
                self.assertEqual(client.post("/api/cash/open", headers=H, json={"opening_cash": 10000}).status_code, 503)
        journal.assert_not_called()
        state.assert_not_called()

    def test_committed_release_accepts_cashier_writes_if_logging_fails(self):
        home, release_id, _, client, _, _ = self.candidate()
        self.assertTrue(client.get("/api/health").json()["ok"])
        releases.confirm(home, release_id)
        with patch.object(releases, "log_event", side_effect=OSError("synthetic log append failure")):
            with self.assertRaises(OSError):
                releases.finish_journal(home, release_id)
        self.assertEqual(releases.read_journal(home)["state"], "done")
        self.assertEqual(client.post("/api/cash/open", headers=H, json={"opening_cash": 10000}).status_code, 201)
