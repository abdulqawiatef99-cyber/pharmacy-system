"""Safety engine infrastructure tests.

Interaction/limit rules here use FICTIONAL ingredients ('Testium', 'Mockazole')
and a fictional source on purpose: the tests prove the machinery, they are not
clinical content and must never be copied into production data."""
import sqlite3

from app.catalog import service as cat
from app.core import accounts
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.safety.engine import SafetyEngine, evaluate
from tests.helpers import DBTestCase


class SafetyCase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.se = SafetyEngine(self.db)
        mk = lambda n, **kw: cat.create_item(self.db, self.ctx, {"name_en": n, **kw}, force=True)
        self.a = mk("Testium 500mg 20 tab")
        self.b = mk("Testium Plus 20 tab")
        self.c = mk("Mockazole 10mg 10 tab")
        self.d = mk("Unmapped Syrup 100ml")
        self.a250 = mk("Testium 250mg 20 tab")
        self.se.set_item_ingredients(self.ctx, self.a, [("Testium", "500mg")])
        self.se.set_item_ingredients(self.ctx, self.b, [("Testium", "250mg"), ("Helperine", "30mg")])
        self.se.set_item_ingredients(self.ctx, self.c, [("Mockazole", "10mg")])
        for i in (self.a, self.b, self.c):
            self.se.verify_item_ingredients(self.ctx, i)
        self.src = self.se.add_source(self.ctx, name="FICTIONAL TEST SOURCE", version="0",
                                      license="test only")

    def ing(self, name):
        return self.db.one("SELECT id FROM active_ingredients WHERE name_en=?", (name,))[0]

    def codes(self, findings):
        return [f.code for f in findings if f.code != "interaction_check_unavailable"]


class TestEngine(SafetyCase):
    def test_duplicate_ingredient_detected(self):
        f = self.se.check(self.ctx, [{"item_id": self.a}, {"item_id": self.b}])
        dup = [x for x in f if x.code == "duplicate_ingredient"]
        self.assertEqual(len(dup), 1)
        self.assertIn("بتركيزات مختلفة", dup[0].message)
        self.assertEqual(dup[0].requires, "override")

    def test_unmapped_item_is_never_safe(self):
        f = self.se.check(self.ctx, [{"item_id": self.d}])
        self.assertEqual(self.codes(f), ["insufficient_data"])

    def test_unverified_mapping_is_ignored(self):
        self.se.set_item_ingredients(self.ctx, self.d, [("Testium", "100mg")])  # not verified
        f = self.se.check(self.ctx, [{"item_id": self.a}, {"item_id": self.d}])
        self.assertNotIn("duplicate_ingredient", self.codes(f))
        self.assertIn("insufficient_data", self.codes(f))

    def test_lookalike_info(self):
        f = self.se.check(self.ctx, [{"item_id": self.a}])
        la = [x for x in f if x.code == "lookalike"]
        self.assertTrue(la and la[0].basis == "heuristic")
        self.assertIn(self.a250, la[0].item_ids)

    def test_no_interaction_without_approved_rule(self):
        f = self.se.check(self.ctx, [{"item_id": self.a}, {"item_id": self.c}])
        self.assertNotIn("interaction", self.codes(f))
        # ...and the result says explicitly that interactions were NOT checked
        self.assertIn("interaction_check_unavailable", [x.code for x in f])

    def test_interaction_rule_lifecycle(self):
        with self.assertRaises(ValidationFailed):     # clinical rule needs a source
            self.se.create_rule(self.ctx, rule_type="interaction", severity="critical",
                                title="x", explanation="x",
                                params={"ingredients": [self.ing("Testium"), self.ing("Mockazole")]},
                                source_id=None)
        rid = self.se.create_rule(self.ctx, rule_type="interaction", severity="critical",
                                  title="Testium + Mockazole", explanation="fictional",
                                  params={"ingredients": [self.ing("Testium"), self.ing("Mockazole")]},
                                  source_id=self.src, source_ref="p.1")
        lines = [{"item_id": self.a}, {"item_id": self.c}]
        self.assertNotIn("interaction", self.codes(self.se.check(self.ctx, lines)))  # draft
        with self.assertRaises(ValidationFailed):     # sole approver must justify
            self.se.approve_rule(self.ctx, rid)
        self.se.approve_rule(self.ctx, rid, self_approval_reason="راجعها د. فلان من المرجع")
        f = self.se.check(self.ctx, lines)
        inter = [x for x in f if x.code == "interaction"][0]
        self.assertEqual((inter.severity, inter.requires), ("critical", "block"))
        self.assertEqual(inter.source["name"], "FICTIONAL TEST SOURCE")
        self.se.disable_rule(self.ctx, rid, "تم اكتشاف خطأ")
        self.assertNotIn("interaction", self.codes(self.se.check(self.ctx, lines)))

    def test_four_eyes_when_second_approver_exists(self):
        accounts.add_user(self.db, self.ctx, username="owner2", full_name="o2",
                          password="Owner2-Pass-2026", role_code="owner")
        rid = self.se.create_rule(self.ctx, rule_type="duplicate_ingredient", severity="moderate",
                                  title="dup", explanation="policy", params={}, source_id=None)
        with self.assertRaises(Forbidden) as e:
            self.se.approve_rule(self.ctx, rid, self_approval_reason="x" * 20)
        self.assertEqual(e.exception.code, "four_eyes")
        self.se.approve_rule(self.login("owner2", "Owner2-Pass-2026"), rid)
        f = self.se.check(self.ctx, [{"item_id": self.a}, {"item_id": self.b}])
        self.assertEqual([x.severity for x in f if x.code == "duplicate_ingredient"], ["moderate"])

    def test_versioning_supersedes(self):
        r1 = self.se.create_rule(self.ctx, rule_type="max_qty", severity="high", title="limit",
                                 explanation="e", params={"item_id": self.a, "max_base_units": 10},
                                 source_id=self.src)
        self.se.approve_rule(self.ctx, r1, self_approval_reason="مراجعة ذاتية موثقة")
        r2 = self.se.create_rule(self.ctx, rule_type="max_qty", severity="high", title="limit",
                                 explanation="e", params={"item_id": self.a, "max_base_units": 20},
                                 source_id=self.src, supersedes=r1)
        self.se.approve_rule(self.ctx, r2, self_approval_reason="مراجعة ذاتية موثقة")
        st = {r["id"]: (r["status"], r["version"]) for r in self.db.all("SELECT * FROM safety_rules")}
        self.assertEqual(st[r1], ("disabled", 1))
        self.assertEqual(st[r2], ("approved", 2))
        f = self.se.check(self.ctx, [{"item_id": self.a, "base_qty": 15}])
        self.assertNotIn("max_qty", self.codes(f))
        with self.assertRaises(Conflict):
            self.se.edit_approved_rule(self.ctx, r2)

    def test_partial_pack_default_policy(self):
        # launch day 21/11 (owner): a strip of a box is a normal sale, no notice without a forbidding policy
        f = self.se.check(self.ctx, [{"item_id": self.a, "partial_pack": True}])
        self.assertNotIn("split_unknown", self.codes(f))
        src = self.se.add_source(self.ctx, name="سياسة الصيدلية الداخلية", version="1", license="internal")
        rid = self.se.create_rule(self.ctx, rule_type="split_policy", severity="moderate",
                                  title="بيع الشريط", explanation="مسموح بيع شريط مع النشرة",
                                  params={"scope": "all_items", "allow_partial_pack": True,
                                          "allow_tablet_split": False}, source_id=src)
        self.se.approve_rule(self.ctx, rid, self_approval_reason="سياسة الصيدلية المعتمدة")
        f = self.se.check(self.ctx, [{"item_id": self.a, "partial_pack": True}])
        self.assertNotIn("split_unknown", self.codes(f))
        f = self.se.check(self.ctx, [{"item_id": self.a, "tablet_split": True}])
        self.assertIn("split_forbidden", self.codes(f))

    def test_split_policy_absent_means_ask(self):
        f = self.se.check(self.ctx, [{"item_id": self.a, "tablet_split": True}])
        self.assertIn("split_unknown", self.codes(f))
        rid = self.se.create_rule(self.ctx, rule_type="split_policy", severity="high",
                                  title="no split", explanation="fictional",
                                  params={"item_id": self.a, "allow_partial_pack": True,
                                          "allow_tablet_split": False}, source_id=self.src)
        self.se.approve_rule(self.ctx, rid, self_approval_reason="مراجعة ذاتية موثقة")
        f = self.se.check(self.ctx, [{"item_id": self.a, "tablet_split": True, "partial_pack": True}])
        self.assertIn("split_forbidden", self.codes(f))
        self.assertNotIn("split_unknown", self.codes(f))


class TestOverrides(SafetyCase):
    def test_assistant_cannot_override_or_disable(self):
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a",
                          password="Assist-Pass-2026", role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        f = self.se.check(actx, [{"item_id": self.a}, {"item_id": self.b}])
        dup_i = [i for i, x in enumerate(f) if x.code == "duplicate_ingredient"][0]
        decisions = {i: "مقصود" for i in range(len(f))}
        self.assertFalse(self.se.resolve(actx, f, decisions))
        denied = self.db.one("SELECT count(*) FROM safety_events WHERE decision='override_denied'")[0]
        self.assertEqual(denied, 1)
        rid = self.se.create_rule(self.ctx, rule_type="duplicate_ingredient", severity="high",
                                  title="d", explanation="d", params={}, source_id=None)
        with self.assertRaises(Forbidden):
            self.se.disable_rule(actx, rid, "مش عايزها")
        self.assertTrue(dup_i >= 0)

    def test_pharmacist_override_with_reason_is_logged(self):
        f = self.se.check(self.ctx, [{"item_id": self.a}, {"item_id": self.b}])
        self.assertFalse(self.se.resolve(self.ctx, f, {}))          # nothing decided
        ok = self.se.resolve(self.ctx, f, {i: "جمع علاجي مقصود بوصفة طبيب" for i in range(len(f))})
        self.assertTrue(ok)
        r = self.db.one("SELECT decision, reason FROM safety_events WHERE decision='overridden'")
        self.assertEqual(r["reason"], "جمع علاجي مقصود بوصفة طبيب")

    def test_events_append_only(self):
        self.se.check(self.ctx, [{"item_id": self.d}])
        with self.assertRaises(sqlite3.DatabaseError):
            with self.db.transaction():
                self.db.execute("DELETE FROM safety_events")

    def test_tenant_isolation(self):
        _, other = self.second_tenant()
        from app.core.errors import NotFound
        with self.assertRaises(NotFound):
            self.se.check(other, [{"item_id": self.a}])


class TestBenchmarkHarness(SafetyCase):
    def test_measures_sensitivity_and_false_positives(self):
        cases = [
            {"lines": [{"item_id": self.a}, {"item_id": self.b}], "expected": ["duplicate_ingredient"]},
            {"lines": [{"item_id": self.a}, {"item_id": self.c}], "expected": []},
            {"lines": [{"item_id": self.d}], "expected": ["insufficient_data"]},
            {"lines": [{"item_id": self.a}, {"item_id": self.c}], "expected": ["interaction"]},  # no rule yet
        ]
        r = evaluate(self.se, self.ctx, cases)
        self.assertEqual(r["true_positives"], 2)
        self.assertEqual(r["false_positives"], 0)
        self.assertEqual(r["missed"], [{"case": 3, "missed": ["interaction"]}])
        self.assertAlmostEqual(r["sensitivity"], 2 / 3)


class TestQuantityLimitAndCriticalBlock(SafetyCase):
    """The quantity-limit rule and the critical "block" decision had no test. Fictional ingredients and
    source, as in the rest of this file: machinery only, never clinical content."""

    def limit(self, severity="high", **params):
        rid = self.se.create_rule(self.ctx, rule_type="max_qty", severity=severity, title="Testium limit",
                                  explanation="fictional", params=params, source_id=self.src, source_ref="p.2")
        self.se.approve_rule(self.ctx, rid, self_approval_reason="مراجعة تجريبية بمرجع وهمي")
        return rid

    def limits(self, lines, ctx=None):
        return [f for f in self.se.check(ctx or self.ctx, lines) if f.code == "max_qty"]

    def events(self, decision):
        return self.db.one("SELECT count(*) n FROM safety_events WHERE org_id=? AND finding_code='max_qty' AND decision=?",
                           (self.ctx.org_id, decision))["n"]

    def test_an_item_limit_is_quiet_up_to_the_limit_and_asks_above_it(self):
        self.limit(item_id=self.a, max_base_units=40)
        self.assertEqual(self.limits([{"item_id": self.a, "base_qty": 40}]), [])
        f = self.limits([{"item_id": self.a, "base_qty": 41}])
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].severity, f[0].requires), ("high", "override"))
        self.assertIn("41", f[0].message)
        self.assertEqual(f[0].source["name"], "FICTIONAL TEST SOURCE")

    def test_an_ingredient_limit_adds_up_every_item_with_it(self):
        self.limit(ingredient_id=self.ing("Testium"), max_base_units=30)
        self.assertEqual(self.limits([{"item_id": self.a, "base_qty": 20}, {"item_id": self.c, "base_qty": 50}]), [])
        f = self.limits([{"item_id": self.a, "base_qty": 20}, {"item_id": self.b, "base_qty": 11}])   # 31 Testium
        self.assertEqual(len(f), 1)

    def test_a_disabled_or_draft_limit_is_not_applied(self):
        draft = self.se.create_rule(self.ctx, rule_type="max_qty", severity="high", title="draft", explanation="x",
                                    params={"item_id": self.a, "max_base_units": 1}, source_id=self.src)
        self.assertEqual(self.limits([{"item_id": self.a, "base_qty": 5}]), [])
        self.se.approve_rule(self.ctx, draft, self_approval_reason="مراجعة تجريبية بمرجع وهمي")
        self.assertEqual(len(self.limits([{"item_id": self.a, "base_qty": 5}])), 1)
        self.se.disable_rule(self.ctx, draft, "تجربة التعطيل")
        self.assertEqual(self.limits([{"item_id": self.a, "base_qty": 5}]), [])

    def test_a_limit_needs_a_source_an_item_or_ingredient_and_a_positive_maximum(self):
        for params, source in (({"item_id": self.a, "max_base_units": 10}, None), ({"max_base_units": 10}, self.src),
                               ({"item_id": self.a, "max_base_units": 0}, self.src)):
            with self.subTest(params=params, source=source):
                with self.assertRaises(ValidationFailed):
                    self.se.create_rule(self.ctx, rule_type="max_qty", severity="high", title="x", explanation="x",
                                        params=params, source_id=source)

    def test_a_high_limit_is_overridden_only_by_who_may_and_with_a_reason(self):
        self.limit(item_id=self.a, max_base_units=10)
        accounts.add_user(self.db, self.ctx, username="asst", full_name="a", password="Assist-Pass-2026",
                          role_code="assistant")
        actx = self.login("asst", "Assist-Pass-2026")
        f = self.limits([{"item_id": self.a, "base_qty": 11}], actx)
        self.assertFalse(self.se.resolve(actx, f, {0: "العميل محتاج"}))           # an assistant may not
        self.assertFalse(self.se.resolve(self.ctx, f, {0: "ok"}))                 # too short a reason
        self.assertEqual(self.events("override_denied"), 2)
        self.assertTrue(self.se.resolve(self.ctx, f, {0: "روشتة طبيب بالكمية"}))
        self.assertEqual(self.events("overridden"), 1)

    def test_a_critical_finding_blocks_unless_the_owner_writes_a_full_reason(self):
        self.limit(severity="critical", item_id=self.a, max_base_units=10)
        accounts.add_user(self.db, self.ctx, username="pharm", full_name="p", password="Pharm-Pass-2026",
                          role_code="pharmacist")
        pctx = self.login("pharm", "Pharm-Pass-2026")
        f = self.limits([{"item_id": self.a, "base_qty": 11}])
        self.assertEqual(f[0].requires, "block")
        self.assertFalse(self.se.resolve(pctx, f, {0: "روشتة طبيب بالكمية دي"}))   # pharmacist: never
        self.assertFalse(self.se.resolve(self.ctx, f, {0: "روشتة"}))              # owner: under 10 letters
        self.assertFalse(self.se.resolve(self.ctx, f, {}))                        # no decision at all
        self.assertEqual(self.events("blocked"), 3)
        self.assertTrue(self.se.resolve(self.ctx, f, {0: "روشتة طبيب مختص بالكمية دي، اتراجعت"}))
        self.assertEqual(self.events("overridden"), 1)
