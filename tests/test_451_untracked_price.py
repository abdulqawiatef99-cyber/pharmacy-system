"""Codex P1 on #451 (26/11): a receipt with no batch number or expiry, approved without `items.price`, joined the
untracked balance that still carries an older public price, so the new boxes sold at the old price. They must go
to a lot at today's price and leave the old boxes at theirs. Synthetic data only."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.catalog import service as catalog
from app.core.permissions import DEFAULT_ROLES
from app.inventory import service as inventory
from app.purchasing.service import Purchasing
from app.sales.service import local_today
from tests.test_api import APICase


class TestUntrackedReceiptWithoutPriceAuthority(APICase):
    def test_new_untracked_boxes_go_to_todays_price_not_the_old_untracked_one(self):
        item = catalog.create_item(self.db, self.ctx, {"name_en": "Synthetic Untracked 20TAB", "pack_size": 2,
                                                       "public_price": 12000})
        with self.db.transaction():                       # old boxes, no batch, still priced at 100.00
            inventory.post_movement(self.db, self.ctx, item_id=item, qty_delta=10, movement_type="opening",
                                    unit_cost=Decimal("20"))
            self.db.execute("UPDATE items SET untracked_public_price=10000 WHERE id=?", (item,))
        supplier = catalog.create_supplier(self.db, self.ctx, name="Synthetic Supplier", supplier_type="company")
        p = Purchasing(self.db, self.s)
        sid = p.create_manual(self.ctx, supplier_id=supplier)
        p.update_header(self.ctx, sid, {"invoice_no": "SYN-UNTRACKED"}, None)
        p.add_line(self.ctx, sid, {"item_id": item, "qty": 2, "public_price": 12000, "discount_bp": 2000}, None)
        p.update_header(self.ctx, sid, {"declared_total": 19200}, None)
        line = p.get(self.ctx, sid)["lines"][0]
        self.assertFalse(line["batch_no"] or line["expiry_date"])
        assistant = replace(self.ctx, role_code="assistant", permissions=frozenset(DEFAULT_ROLES["assistant"][1]))
        approver = replace(assistant, approver_id=self.ctx.user_id,
                           approver_permissions=frozenset({"purchases.approve"}))
        self.assertFalse(approver.allowed("items.price"))
        p.approve(approver, sid, p.get(self.ctx, sid)["invoice"]["version"])

        received = self.db.one("SELECT batch_id FROM stock_movements WHERE org_id=? AND item_id=?"
                               " AND movement_type='purchase'", (self.ctx.org_id, item))
        self.assertIsNotNone(received["batch_id"], "new boxes joined the untracked balance at the old price")
        lot = self.db.one("SELECT public_price FROM batches WHERE id=?", (received["batch_id"],))
        self.assertEqual(lot["public_price"], 12000)
        old = self.db.one("SELECT coalesce(sum(qty), 0) q FROM stock_balances WHERE org_id=? AND item_id=?"
                          " AND batch_key=''", (self.ctx.org_id, item))
        self.assertEqual(old["q"], 10)                    # the old boxes stay apart, at their own price
        self.assertEqual(self.db.one("SELECT untracked_public_price p FROM items WHERE id=?", (item,))["p"], 10000)
