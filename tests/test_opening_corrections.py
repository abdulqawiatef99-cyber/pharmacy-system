"""Part 3 item 2: correcting a supplier's or customer's balance after the E-Stock move, from a screen.

Owner and manager by default (permission `balances.correct`, switchable per person), a written reason, the
difference posted as an opening adjustment through `reconcile.post_adjustment` (so it is in the
reconciliation history and the audit log), and never twice on a retry or a stale screen."""
from dataclasses import replace

from starlette.testclient import TestClient

from app import config
from app.api.app import create_app
from app.core import people
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.dataimport import opening_balances as ob
from app.purchasing.service import Purchasing
from tests.test_estock_db_import import Base

H = {"X-Requested-With": "pharm"}
PW = "Staff-Pass-2026"


class Case(Base):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run_import()["ok"])
        self.v1 = self.db.one("SELECT id FROM suppliers WHERE name='مورد تجربة'")["id"]
        self.c1 = self.db.one("SELECT id FROM customers WHERE name='أحمد تجربة'")["id"]

    def staff(self, username, role):
        uid = people.add_user(self.db, self.ctx, username=username, full_name=username, password=PW, role_code=role)
        return uid, self.login(username, PW)


class TestCorrection(Case):
    def test_manager_corrects_with_a_reason_and_it_is_an_opening_adjustment(self):
        _, mgr = self.staff("mgr", "manager")
        rows = ob.corrections(self.db, mgr, "supplier")
        v1 = next(r for r in rows if r["party_id"] == self.v1)
        self.assertEqual((v1["opening"], v1["corrections"]), (50000, 0))
        got = ob.correct(self.db, mgr, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف حساب المورد")
        self.assertEqual((got["estock_opening"], got["opening_adjustments"], got["opening"], got["balance_now"]),
                         (50000, -2500, 47500, 47500))
        self.assertEqual([(h["amount"], h["by_name"]) for h in got["history"]], [(-2500, "mgr")])
        self.assertIn("كشف حساب المورد", got["history"][0]["note"])
        # one ledger row, kind adjustment, never a payment; one reconciliation row of type opening
        led = self.db.all("SELECT amount, kind, ref_id FROM supplier_ledger WHERE supplier_id=? ORDER BY amount", (self.v1,))
        self.assertEqual([(r["amount"], r["kind"], r["ref_id"]) for r in led][0], (-2500, "adjustment", ob.REF))
        rec = self.db.all("SELECT difference, adj_type, statement_balance FROM party_reconciliations WHERE party_id=?",
                          (self.v1,))
        self.assertEqual([tuple(r) for r in rec], [(-2500, "opening", 47500)])
        acts = {r["action"] for r in self.db.all("SELECT action FROM audit_log WHERE entity_id=?", (self.v1,))}
        self.assertTrue({"estock.opening_correct", "supplier.reconcile"} <= acts)
        self.assertEqual(ob.corrections(self.db, mgr, "supplier", q="V1")[0]["corrections"], 1)

    def test_customer_correction_counts_on_the_opening_not_later_activity(self):
        from app.sales.service import Sales
        Sales(self.db).customer_payment(self.ctx, self.c1, 2000, method="card")
        got = ob.correct(self.db, self.ctx, "customer", self.c1, 11000, seen_opening=12000, seen_revision=0, reason="العميل أكد")
        self.assertEqual((got["opening"], got["opening_adjustments"], got["balance_now"]), (11000, -1000, 9000))

    def test_a_stale_screen_or_a_retry_posts_nothing(self):
        ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف حساب")
        with self.assertRaises(Conflict) as e:                 # the same request again (the screen still says 500)
            ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف حساب")
        self.assertEqual(e.exception.code, "balance_changed")
        n = self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=? AND ref_id=?", (self.v1, ob.REF))["n"]
        self.assertEqual(n, 1)

    def test_a_stale_screen_after_the_opening_was_put_back_posts_nothing(self):
        """Codex P1 on #394: 500 -> 475 -> 500, then the first request replayed from an old screen."""
        ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف")
        ob.correct(self.db, self.ctx, "supplier", self.v1, 50000, seen_opening=47500, seen_revision=1, reason="رجوع")
        with self.assertRaises(Conflict):
            ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف")
        n = self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=? AND ref_id=?", (self.v1, ob.REF))["n"]
        self.assertEqual(n, 2)

    def test_the_reconciliation_row_is_on_the_opening_basis(self):
        """Codex P2 on #394: a payment after the move; the recorded statement - program = the difference."""
        from app.sales.service import Sales
        Sales(self.db).customer_payment(self.ctx, self.c1, 2000, method="card")
        ob.correct(self.db, self.ctx, "customer", self.c1, 11000, seen_opening=12000, seen_revision=0, reason="العميل أكد")
        r = self.db.one("SELECT statement_balance, program_balance, difference FROM party_reconciliations WHERE party_id=?",
                        (self.c1,))
        self.assertEqual((r["statement_balance"], r["program_balance"], r["difference"]), (11000, 12000, -1000))

    def test_refusals(self):
        with self.assertRaises(ValidationFailed) as e:
            ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason=" ")
        self.assertEqual(e.exception.code, "reason_required")
        with self.assertRaises(ValidationFailed) as e:
            ob.correct(self.db, self.ctx, "supplier", self.v1, 50000, seen_opening=50000, seen_revision=0, reason="نفس الرقم")
        self.assertEqual(e.exception.code, "no_change")
        for bad in (True, 12.5, "100", 10 ** 12):
            with self.assertRaises(ValidationFailed):
                ob.correct(self.db, self.ctx, "supplier", self.v1, bad, seen_opening=50000, seen_revision=0, reason="سبب")
        from app.catalog import service as cat
        other = cat.create_supplier(self.db, self.ctx, name="مورد جديد")
        with self.assertRaises(NotFound):
            ob.correct(self.db, self.ctx, "supplier", other, 100, seen_opening=0, seen_revision=0, reason="سبب")
        self.assertFalse(self.db.one("SELECT 1 FROM supplier_ledger WHERE ref_id=?", (ob.REF,)))

    def test_permission_is_per_person(self):
        uid, ph = self.staff("pharm1", "pharmacist")
        with self.assertRaises(Forbidden):
            ob.corrections(self.db, ph, "supplier")
        people.set_exception(self.db, self.ctx, uid, "balances.correct", "grant")
        people.set_exception(self.db, self.ctx, uid, "suppliers.pay", "grant")
        ph = self.login("pharm1", PW)
        ob.correct(self.db, ph, "supplier", self.v1, 49000, seen_opening=50000, seen_revision=0, reason="سبب مكتوب")
        mid, mgr = self.staff("mgr2", "manager")
        people.set_exception(self.db, self.ctx, mid, "balances.correct", "deny")
        with self.assertRaises(Forbidden):
            ob.corrections(self.db, self.login("mgr2", PW), "customer")

    def test_owner_confirm_after_a_correction_still_closes_the_import(self):
        ob.correct(self.db, self.ctx, "supplier", self.v1, 47500, seen_opening=50000, seen_revision=0, reason="كشف")
        with self.assertRaises(Conflict):
            self.run_import(folder=self._last)


class TestCorrectionAPI(Case):
    def setUp(self):
        super().setUp()
        s = replace(config.load(), upload_dir=self.tmp / "up", anthropic_api_key=None, data_dir=self.tmp,
                    backup_dir=self.tmp / "backups")
        self.c = TestClient(create_app(self.db, s, Purchasing(self.db, s)))
        self.staff("mgr", "manager")
        r = self.c.post("/api/auth/login", json={"username": "mgr", "password": PW}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)

    def test_list_correct_history_and_a_replayed_key(self):
        rows = self.c.get("/api/opening-balances/supplier", headers=H).json()["parties"]
        self.assertIn(self.v1, [r["party_id"] for r in rows])
        body = {"amount": 47500, "seen_opening": 50000, "seen_revision": 0, "reason": "كشف المورد"}
        hk = {**H, "Idempotency-Key": "k-1"}
        r1 = self.c.post(f"/api/opening-balances/supplier/{self.v1}", json=body, headers=hk)
        self.assertEqual(r1.status_code, 201, r1.text)
        r2 = self.c.post(f"/api/opening-balances/supplier/{self.v1}", json=body, headers=hk)
        self.assertEqual(r2.json()["opening"], 47500)
        n = self.db.one("SELECT count(*) n FROM supplier_ledger WHERE supplier_id=? AND ref_id=?", (self.v1, ob.REF))["n"]
        self.assertEqual(n, 1)
        h = self.c.get(f"/api/opening-balances/supplier/{self.v1}", headers=H).json()["history"]
        self.assertEqual(len(h), 1)
        bad = self.c.post(f"/api/opening-balances/supplier/{self.v1}", json={"amount": 1, "reason": "x y z"}, headers=H)
        self.assertGreaterEqual(bad.status_code, 400)
        self.assertEqual(self.db.one("SELECT count(*) n FROM supplier_ledger WHERE ref_id=?", (ob.REF,))["n"], 1)
