"""Stock service must not attach another item/pharmacy's batch to a movement."""
from app.catalog import service as catalog
from app.core.errors import NotFound
from app.inventory import service as inventory
from tests.helpers import DBTestCase


class TestInventoryBatchScope(DBTestCase):
    def setUp(self):
        super().setUp()
        self.item = catalog.create_item(self.db, self.ctx, {'name_en': 'Synthetic item Alpha'})
        self.other_item = catalog.create_item(self.db, self.ctx, {'name_en': 'Synthetic item Beta'})
        _, self.other_ctx = self.second_tenant()
        self.foreign_item = catalog.create_item(self.db, self.other_ctx, {'name_en': 'Synthetic foreign item'})
        with self.db.transaction():
            self.batch = inventory.get_or_create_batch(self.db, self.ctx, self.item, 'A', '2030-12-31')
            self.wrong_batch = inventory.get_or_create_batch(self.db, self.ctx, self.other_item, 'B', '2030-12-31')
            self.foreign_batch = inventory.get_or_create_batch(self.db, self.other_ctx, self.foreign_item, 'F', '2030-12-31')

    def test_movement_refuses_batch_of_another_item(self):
        with self.assertRaises(NotFound), self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=self.wrong_batch,
                                    qty_delta=2, movement_type='opening')
        self.assertEqual(self.db.one('SELECT count(*) FROM stock_movements')[0], 0)
        self.assertEqual(self.db.one('SELECT count(*) FROM stock_balances')[0], 0)

    def test_movement_refuses_batch_of_another_pharmacy(self):
        with self.assertRaises(NotFound), self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=self.foreign_batch,
                                    qty_delta=2, movement_type='opening')
        self.assertEqual(self.db.one('SELECT count(*) FROM stock_movements')[0], 0)

    def test_batch_creation_refuses_foreign_item(self):
        count = self.db.one('SELECT count(*) FROM batches')[0]
        with self.assertRaises(NotFound), self.db.transaction():
            inventory.get_or_create_batch(self.db, self.ctx, self.foreign_item, 'INVALID', '2030-12-31')
        self.assertEqual(self.db.one('SELECT count(*) FROM batches')[0], count)

    def test_batch_creation_refuses_missing_item_even_without_batch_metadata(self):
        with self.assertRaises(NotFound), self.db.transaction():
            inventory.get_or_create_batch(self.db, self.ctx, 'missing-item', None, None)

    def test_valid_tracked_and_untracked_stock_remain_supported(self):
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.item, batch_id=self.batch,
                                    qty_delta=3, movement_type='opening')
            inventory.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=2, movement_type='opening')
        self.assertEqual(inventory.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.item, self.batch), 3)
        self.assertEqual(inventory.batch_qty(self.db, self.ctx.org_id, self.ctx.branch_id, self.item, None), 2)
        self.assertEqual(inventory.verify_balances(self.db, self.ctx.org_id), [])
