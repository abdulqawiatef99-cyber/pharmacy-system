"""U2-21 dispensing notes: the five EDA 375/2026 ingredients on by default, others start off «محتاج تأكيد»,
once per invoice and never blocking, owner-only settings, per pharmacy."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as cat
from app.core.errors import Forbidden, ValidationFailed
from app.inventory import service as inv
from app.safety import dispensing
from tests.test_sales import TODAY, SalesCase


class TestDispensingNotes(SalesCase):
    def setUp(self):
        super().setUp()
        self.gaba = cat.create_item(self.db, self.ctx, {"name_en": "Neurontin 300mg 50 cap", "active_ingredient": "Gabapentin",
                                                        "public_price": 10000})
        self.pre = cat.create_item(self.db, self.ctx, {"name_en": "Lyrica 75mg 14 cap", "active_ingredient": "Pregabalin",
                                                       "public_price": 10000})
        with self.db.transaction():
            for it in (self.gaba, self.pre):
                b = inv.get_or_create_batch(self.db, self.ctx, it, "B", (TODAY + timedelta(days=400)).isoformat())
                inv.post_movement(self.db, self.ctx, item_id=it, qty_delta=5, batch_id=b, movement_type="purchase",
                                  unit_cost=Decimal("50"))

    def test_defaults(self):
        n = dispensing.get(self.db, self.ctx.org_id)
        self.assertEqual({k for k, v in n.items() if v["enabled"]},
                         {"gabapentin", "eszopiclone", "cyclopentolate", "tropicamide", "clozapine"})
        self.assertIn("روشتة سارية", n["gabapentin"]["rules"])

    def test_once_per_invoice_and_sale_not_blocked(self):
        sid = self.s.create(self.ctx)
        self.s.add_line(self.ctx, sid, self.gaba, units=1)
        self.s.add_line(self.ctx, sid, self.pan, units=2)
        d = self.s.add_line(self.ctx, sid, self.gaba, units=1)            # the same ingredient twice: one note
        self.assertEqual([n["ingredient"] for n in d["dispensing_notes"]], ["gabapentin"])
        done = self.s.complete(self.ctx, sid, paid_cash=d["total"], safety_decisions=self.ack_all(sid))
        self.assertEqual(done["status"], "completed")
        self.assertNotIn("dispensing_notes", done)

    def test_a_new_ingredient_starts_off_and_on_when_confirmed(self):
        n = dispensing.save(self.db, self.ctx, {"Pregabalin": {"rules": ["روشتة"]}})
        self.assertEqual((n["pregabalin"]["enabled"], n["pregabalin"]["confirmed"]), (False, False))
        sid = self.s.create(self.ctx)
        self.assertEqual(self.s.add_line(self.ctx, sid, self.pre, units=1)["dispensing_notes"], [])
        dispensing.save(self.db, self.ctx, {"pregabalin": {"enabled": True}})
        self.assertEqual([x["ingredient"] for x in self.s.get(self.ctx, sid)["dispensing_notes"]], ["pregabalin"])
        with self.assertRaises(ValidationFailed):
            dispensing.save(self.db, self.ctx, {"gabapentin": {"remove": True}})

    def test_owner_only_and_per_pharmacy(self):
        with self.assertRaises(Forbidden):
            dispensing.save(self.db, replace(self.ctx, permissions=frozenset()), {"x": {"enabled": True}})
        dispensing.save(self.db, self.ctx, {"gabapentin": {"enabled": False}})
        _, other = self.second_tenant()
        self.assertTrue(dispensing.get(self.db, other.org_id)["gabapentin"]["enabled"])

    def test_whole_word_match(self):
        items = [{"active_ingredient": "gabapentinoid-like", "name_en": "X"}, {"active_ingredient": "", "name_en": "Gabapentin 300"}]
        self.assertEqual([n["item"] for n in dispensing.for_items(self.db, self.ctx.org_id, items)], ["Gabapentin 300"])
