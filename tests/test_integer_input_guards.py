"""Do not silently truncate fractional units or integer monetary amounts."""
from app.catalog import service as catalog
from app.core.errors import ValidationFailed
from tests.test_api import APICase, H


class TestIntegerInputGuards(APICase):
    def test_fractional_catalog_pack_size_is_rejected_without_creating_item(self):
        before = self.db.one('SELECT count(*) FROM items')[0]
        with self.assertRaises(ValidationFailed):
            catalog.create_item(self.db, self.ctx, {'name_en': 'Synthetic fractional pack', 'pack_size': 2.5})
        self.assertEqual(self.db.one('SELECT count(*) FROM items')[0], before)

    def test_fractional_price_update_is_rejected_without_history_change(self):
        before = catalog.get_item(self.db, self.ctx, self.pan)['public_price']
        count = self.db.one('SELECT count(*) FROM item_price_history')[0]
        with self.assertRaises(ValidationFailed):
            catalog.update_item(self.db, self.ctx, self.pan, {'public_price': 4000.9})
        self.assertEqual(catalog.get_item(self.db, self.ctx, self.pan)['public_price'], before)
        self.assertEqual(self.db.one('SELECT count(*) FROM item_price_history')[0], count)

    def test_fractional_cash_float_is_rejected_without_opening_shift(self):
        self.http_login()
        response = self.c.post('/api/cash/open', headers=H, json={'opening_cash': 100.9})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.one('SELECT count(*) FROM cash_shifts')[0], 0)

    def test_boolean_is_not_an_integer_quantity(self):
        with self.assertRaises(ValidationFailed):
            catalog.update_item(self.db, self.ctx, self.pan, {'pack_size': True})

    def test_whole_numeric_and_legacy_integer_strings_remain_accepted(self):
        for value in (3, 3.0, '3'):
            with self.subTest(value=value):
                updated = catalog.update_item(self.db, self.ctx, self.pan, {'pack_size': value})
                self.assertEqual(updated['pack_size'], 3)

    def test_fractional_sale_units_are_rejected_without_adding_line(self):
        from app.inventory import service as inventory
        with self.db.transaction():
            inventory.post_movement(self.db, self.ctx, item_id=self.pan, qty_delta=10, movement_type='opening')
        self.http_login()
        draft = self.c.post('/api/sales', headers=H, json={}).json()
        response = self.c.post(f"/api/sales/{draft['id']}/lines", headers=H,
                               json={'item_id': self.pan, 'units': 1.9})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.db.one('SELECT count(*) FROM sale_lines')[0], 0)

    def test_nonfinite_and_fractional_numbers_are_rejected_by_shared_parser(self):
        from decimal import Decimal
        from app.core.numbers import whole_number
        for value in (float('inf'), float('-inf'), float('nan'), Decimal('NaN'),
                      Decimal('Infinity'), Decimal('1.2'), -0.5, True, False):
            with self.subTest(value=str(value)):
                with self.assertRaises((ValueError, TypeError)):
                    whole_number(value)
        for value in (0, -2, 2.0, Decimal('2.0'), '002'):
            self.assertEqual(whole_number(value), int(value))

    def test_omitted_or_empty_cash_float_retains_legacy_zero_default(self):
        self.http_login()
        response = self.c.post('/api/cash/open', headers=H, json={'opening_cash': ''})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['opening_cash'], 0)
