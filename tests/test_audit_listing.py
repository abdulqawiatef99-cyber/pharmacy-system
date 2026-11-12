"""The owner's "who did what" log (audit.listing, GET /api/audit) and how an approver is written
into an audit row. Before this file only the group filter was tested. Synthetic data only."""
import json
from dataclasses import replace
from datetime import date

from app.core import audit, people
from app.core.errors import Forbidden, ValidationFailed
from tests.helpers import DBTestCase
from tests.test_api import APICase, H

PW = "Staff-Pass-2026"


class Listing(DBTestCase):
    def setUp(self):
        super().setUp()
        self.mgr_id = people.add_user(self.db, self.ctx, username="mgr1", full_name="Manager One", password=PW,
                                      role_code="manager")
        people.add_user(self.db, self.ctx, username="ph1", full_name="Pharm One", password=PW, role_code="pharmacist")
        self.today = date.today().isoformat()

    def record(self, ctx, action, after=None):
        with self.db.transaction():
            audit.record(self.db, ctx, action, "test", "e1", after=after)

    def test_needs_audit_read(self):
        with self.assertRaises(Forbidden):
            audit.listing(self.db, self.login("ph1", PW), self.today, self.today)
        self.assertIsInstance(audit.listing(self.db, self.login("mgr1", PW), self.today, self.today)["rows"], list)

    def test_bad_or_missing_dates_are_refused(self):
        for d1, d2 in (("", ""), ("2026-13-01", self.today), (self.today, "yesterday"), (None, self.today)):
            with self.subTest(d1=d1, d2=d2), self.assertRaises(ValidationFailed):
                audit.listing(self.db, self.ctx, d1, d2)

    def test_user_filter_and_labels(self):
        mgr = self.login("mgr1", PW)
        self.record(mgr, "item.create")
        self.record(self.ctx, "customer.create")
        rows = audit.listing(self.db, self.ctx, self.today, self.today, user_id=self.mgr_id)["rows"]
        self.assertTrue(rows)
        self.assertEqual({r["user_name"] for r in rows}, {"Manager One"})
        self.assertNotIn("customer.create", [r["action"] for r in rows])
        mine = audit.listing(self.db, self.ctx, self.today, self.today, user_id=self.ctx.user_id)["rows"]
        hit = next(r for r in mine if r["action"] == "customer.create")
        self.assertEqual(hit["label"], "عميل جديد")
        self.record(self.ctx, "made.up_action")
        rows = audit.listing(self.db, self.ctx, self.today, self.today)["rows"]
        self.assertEqual(next(r for r in rows if r["action"] == "made.up_action")["label"], "made.up_action")

    def test_group_matches_only_its_own_prefix(self):
        self.record(self.ctx, "sale.complete")
        self.record(self.ctx, "sales_like.other")     # "sale" + "s": not the sale group
        acts = [r["action"] for r in audit.listing(self.db, self.ctx, self.today, self.today, group="sale")["rows"]]
        self.assertIn("sale.complete", acts)
        self.assertNotIn("sales_like.other", acts)

    def test_period_outside_the_rows_is_empty(self):
        self.record(self.ctx, "item.create")
        out = audit.listing(self.db, self.ctx, "2020-01-01", "2020-01-31")
        self.assertEqual((out["rows"], out["truncated"], out["from"], out["to"]), ([], False, "2020-01-01", "2020-01-31"))

    def test_another_pharmacy_never_sees_these_rows(self):
        self.record(self.ctx, "item.create", after={"name": "Secret Item"})
        _, other = self.second_tenant()
        rows = audit.listing(self.db, other, self.today, self.today)["rows"]
        self.assertFalse([r for r in rows if r["after_json"] and "Secret Item" in r["after_json"]])
        mine = audit.listing(self.db, self.ctx, self.today, self.today)["rows"]
        self.assertTrue([r for r in mine if r["after_json"] and "Secret Item" in r["after_json"]])


class ApproverInTheRow(DBTestCase):
    def test_approver_is_added_to_every_kind_of_after(self):
        mgr = people.add_user(self.db, self.ctx, username="mgr2", full_name="M", password=PW, role_code="manager")
        ctx = replace(self.login("mgr2", PW), approver_id=self.ctx.user_id)
        for i, after in enumerate((None, {"a": 1}, 5)):
            with self.db.transaction():
                audit.record(self.db, ctx, "test.approved", "test", f"e{i}", after=after)
        rows = self.db.all("SELECT entity_id, after_json, user_id FROM audit_log WHERE org_id=? AND action=?"
                           " ORDER BY entity_id", (self.ctx.org_id, "test.approved"))
        self.assertEqual([json.loads(r["after_json"]) for r in rows], [
            {"approved_by": self.ctx.user_id},
            {"a": 1, "approved_by": self.ctx.user_id},
            {"value": 5, "approved_by": self.ctx.user_id}])
        self.assertEqual({r["user_id"] for r in rows}, {mgr})          # the actor stays the actor
        self.assertEqual(audit.verify_chain(self.db), [])


class Route(APICase):
    def test_route_passes_the_filters_and_refuses_bad_ones(self):
        self.http_login()
        today = date.today().isoformat()
        r = self.c.get(f"/api/audit?from={today}&to={today}&group=nope", headers=H)
        self.assertEqual(r.status_code, 422, r.text)
        r = self.c.get(f"/api/audit?from={today}&to={today}&group=user", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(all(x["action"].startswith("user") for x in r.json()["rows"]))
        r = self.c.get("/api/audit?from=&to=", headers=H)
        self.assertEqual(r.status_code, 422, r.text)
