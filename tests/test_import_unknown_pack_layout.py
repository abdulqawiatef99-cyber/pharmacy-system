"""QA-007 (#461): a counted product (tablets, capsules...) imported with a blank pack layout is never taken
as a whole-box item silently. The row waits for a decision: the real number of strips, or "whole box only".
Synthetic data only."""
from app.core.errors import Conflict, ValidationFailed
from app.dataimport.service import DataImport
from tests.helpers import DBTestCase
from tests.test_dataimport import sheet

HEAD = ["كود الصنف", "اسم الصنف", "سعر الجمهور", "عدد الوحدات"]
ROWS = [HEAD,
        [201, "Synthofen 400mg 20 tab", 40, None],          # counted, layout blank -> must be asked
        [202, "Boxonly 500mg 20 tab", 30, 1],               # counted, explicitly whole box
        [203, "Syrupex 120ml syrup", 25, None],             # nothing to split
        [204, "Stripped 250mg 30 cap", 60, 3]]              # layout given


class TestUnknownPackLayout(DBTestCase):
    def setUp(self):
        super().setUp()
        self.di = DataImport(self.db)
        self.run_id = self.di.analyze(self.ctx, sheet(ROWS), "items.xlsx")
        self.rows = {r["data"]["code"]: r for r in self.di.get(self.ctx, self.run_id)["rows"]}

    def pack(self, code):
        return self.db.one("SELECT pack_size FROM items WHERE org_id=? AND code=?", (self.ctx.org_id, code))["pack_size"]

    def test_only_the_unknown_layout_needs_a_decision(self):
        self.assertEqual({c: r["action"] for c, r in self.rows.items()},
                         {"201": "review", "202": "create", "203": "create", "204": "create"})
        issue = [i for i in self.rows["201"]["issues"] if i["code"] == "unknown_pack_layout"][0]
        self.assertEqual(issue["count"], 20)
        self.assertEqual(issue["suggest"], [2, 4])
        for code in ("202", "203", "204"):
            self.assertNotIn("unknown_pack_layout", [i["code"] for i in self.rows[code]["issues"]])

    def test_nothing_is_imported_until_the_layout_is_decided(self):
        with self.assertRaises(Conflict) as e:
            self.di.apply(self.ctx, self.run_id)
        self.assertEqual(e.exception.code, "pending_review")
        self.assertEqual(self.db.one("SELECT count(*) c FROM items")["c"], 0)

    def test_a_plain_create_is_refused_it_would_guess_whole_box(self):
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "create")
        with self.assertRaises(Conflict):
            self.di.apply(self.ctx, self.run_id)

    def test_confirming_the_real_pack_enables_the_strip_unit(self):
        self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "pack:2")
        self.di.apply(self.ctx, self.run_id)
        self.assertEqual(self.pack("201"), 2)               # 2 strips: the sale screen offers the strip
        self.assertEqual(self.pack("202"), 1)
        self.assertEqual(self.pack("203"), 1)
        self.assertEqual(self.pack("204"), 3)

    def test_whole_box_only_is_a_decision_too(self):
        self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "pack:1")
        self.di.apply(self.ctx, self.run_id)
        self.assertEqual(self.pack("201"), 1)

    def test_skipping_the_row_is_allowed(self):
        self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "skip")
        self.di.apply(self.ctx, self.run_id)
        self.assertEqual(self.db.one("SELECT count(*) c FROM items WHERE code='201'")["c"], 0)

    def test_impossible_layouts_are_refused(self):
        rid = self.rows["201"]["id"]
        for bad in ("pack:3", "pack:0", "pack:-2", "pack:x", "pack:", "pack:2.5", "pack:40"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationFailed):
                    self.di.decide(self.ctx, self.run_id, rid, bad)
        with self.assertRaises(ValidationFailed):           # a row whose layout is known takes no pack decision
            self.di.decide(self.ctx, self.run_id, self.rows["202"]["id"], "pack:2")

    def test_a_similar_existing_item_still_needs_the_layout_when_created_anyway(self):
        self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "pack:2")
        self.di.apply(self.ctx, self.run_id)
        run = self.di.analyze(self.ctx, sheet([HEAD, [None, "SYNTHOFEN 400 MG 20 TAB", 40, None]]), "again.xlsx")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "review")
        self.assertIn("unknown_pack_layout", [i["code"] for i in row["issues"]])
        with self.assertRaises(ValidationFailed):
            self.di.decide(self.ctx, run, row["id"], "create")
        self.di.decide(self.ctx, run, row["id"], f"link:{row['target_id']}")       # linking keeps the item's pack
        self.di.apply(self.ctx, run)
        self.assertEqual(self.pack("201"), 2)

    def test_updating_an_existing_item_with_a_blank_layout_keeps_its_pack(self):
        self.di.decide(self.ctx, self.run_id, self.rows["201"]["id"], "pack:2")
        self.di.apply(self.ctx, self.run_id)
        run = self.di.analyze(self.ctx, sheet([HEAD, [201, "Synthofen 400mg 20 tab", 45, None]]), "prices.xlsx")
        row = self.di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "update")
        self.di.apply(self.ctx, run)
        self.assertEqual(self.pack("201"), 2)


class TestTheSourceNameIsNoWayAround(DBTestCase):
    """Review P1: "E-Stock" is the preselected source on the import screen. Naming it must not switch the
    question off; only the E-Stock move itself (which sets packs from its own reviewed file) may."""

    def test_a_file_sent_as_estock_is_still_asked(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, sheet(ROWS), "items.xlsx", source_system="estock")
        rows = {r["data"]["code"]: r for r in di.get(self.ctx, run)["rows"]}
        self.assertEqual(rows["201"]["action"], "review")
        self.assertIn("unknown_pack_layout", [i["code"] for i in rows["201"]["issues"]])
        with self.assertRaises(Conflict):
            di.apply(self.ctx, run)

    def test_the_route_cannot_switch_it_off(self):
        import inspect
        from app.api import app as api
        src = inspect.getsource(api)
        self.assertNotIn("packs_set_later", src)             # the flag is internal to the E-Stock move

    def test_the_estock_move_keeps_its_own_pack_step(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, sheet(ROWS), "items.xlsx", source_system="estock", packs_set_later=True)
        self.assertEqual({r["action"] for r in di.get(self.ctx, run)["rows"]}, {"create"})


class TestCodexFindings(DBTestCase):
    def test_the_form_in_its_own_column_is_asked_too(self):
        di = DataImport(self.db)
        rows = [["كود الصنف", "اسم الصنف", "الشكل", "سعر الجمهور", "عدد الوحدات"],
                [401, "OtherSynthetic 400mg 20", "tab", 40, None],
                [402, "LiquidSynthetic 120", "syrup", 25, None]]
        run = di.analyze(self.ctx, sheet(rows), "forms.xlsx")
        got = {r["data"]["code"]: r for r in di.get(self.ctx, run)["rows"]}
        self.assertEqual(got["401"]["action"], "review")
        issue = [i for i in got["401"]["issues"] if i["code"] == "unknown_pack_layout"][0]
        self.assertEqual(issue["count"], 20)
        self.assertEqual(got["402"]["action"], "create")

    def test_the_confirmed_pack_may_equal_an_existing_items_and_still_be_kept_apart(self):
        di = DataImport(self.db)
        run = di.analyze(self.ctx, sheet(ROWS), "items.xlsx")
        first = {r["data"]["code"]: r for r in di.get(self.ctx, run)["rows"]}
        di.decide(self.ctx, run, first["201"]["id"], "pack:2")
        di.apply(self.ctx, run)
        run = di.analyze(self.ctx, sheet([HEAD, [301, "Synthofen 400mg 20 tab", 40, None]]), "second.xlsx")
        row = di.get(self.ctx, run)["rows"][0]
        self.assertEqual(row["action"], "review")
        di.decide(self.ctx, run, row["id"], "pack:2")        # the person keeps it as its own item, same layout
        di.apply(self.ctx, run)                               # used to fail with duplicate_item and roll back
        packs = [r["pack_size"] for r in self.db.all("SELECT pack_size FROM items WHERE code IN ('201','301')")]
        self.assertEqual(packs, [2, 2])
