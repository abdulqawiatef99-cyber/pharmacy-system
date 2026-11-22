"""Shared fixture for the update-2 purchase tests: one supplier, one item (box of 1), a manual draft helper."""
from dataclasses import replace

from app import config
from app.catalog import service as cat
from app.purchasing.service import Purchasing
from tests.helpers import DBTestCase


class PurchaseBase(DBTestCase):
    def setUp(self):
        super().setUp()
        self.p = Purchasing(self.db, replace(config.load(), upload_dir=self.tmp / "u"))
        self.sup = cat.create_supplier(self.db, self.ctx, name="مورد تجربة")
        self.item = cat.create_item(self.db, self.ctx, {"name_en": "Guard Tab", "public_price": 1000})
        self._n = 0

    def draft(self, *, qty, expiry, item=None):
        self._n += 1
        inv_id = self.p.create_manual(self.ctx)
        self.p.update_header(self.ctx, inv_id, {"supplier_id": self.sup, "invoice_no": f"G{self._n}"}, None)
        self.p.add_line(self.ctx, inv_id, {"item_id": item or self.item, "qty": qty, "public_price": "10",
                                           "discount_pct": "20", "unit_net_price": "8", "batch_no": "B1",
                                           "expiry_date": expiry}, None)
        return inv_id
