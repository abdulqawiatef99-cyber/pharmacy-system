"""The E-Stock move at scale (19/11): the synthetic full-size export and the lighter row type. Synthetic only.

The timed full-size run is `python -m app.dataimport.estock_synthetic --bench` (minutes, not in the suite);
here a small slice of the same generator must reconcile 100%, and the rows must not copy the export."""
import tempfile
from pathlib import Path

from app.dataimport import estock_archive, estock_db, estock_synthetic
from tests.helpers import DBTestCase


class TestRow(DBTestCase):
    def test_a_row_reads_as_the_dict_it_replaced(self):
        r = estock_db.Row({"a": 0, "b": 1, "c": 2}, ["1", "x"])           # a short line: c is absent
        self.assertEqual((r["a"], r.get("b"), r.get("c"), r.get("c", "-")), ("1", "x", None, "-"))
        self.assertEqual(dict(r), {"a": "1", "b": "x"})
        self.assertNotIn("c", r)
        self.assertIn("a", r)
        with self.assertRaises(KeyError):
            r["c"]
        self.assertEqual(len(r), 2)

    def test_rows_share_the_export_text(self):
        folder = estock_synthetic.write(Path(tempfile.mkdtemp(dir=self.tmp)) / "run", scale=0.001)
        ex = estock_db.Export(folder)
        head, data = ex.raw("Products")
        rows = ex.rows("Products")
        self.assertIs(rows[0]._v, data[0])                                   # one copy of the table, not two
        self.assertEqual(rows[0]["product_id"], data[0][[h.lower() for h in head].index("product_id")])
        a, b = ex.raw("Products")[1][0], ex.raw("Products")[1][1]
        col = [h.lower() for h in head].index("active")
        self.assertIs(a[col], b[col])                                        # repeated short cells: one object


class TestSyntheticScale(DBTestCase):
    def test_a_slice_of_the_full_size_export_reconciles(self):
        folder = estock_synthetic.write(Path(tempfile.mkdtemp(dir=self.tmp)) / "run", scale=0.02)
        res = estock_db.run(self.db, self.ctx, export_dir=folder, out_dir=self.tmp / "out",
                            archive_path=self.tmp / estock_archive.NAME, write_archive=True)
        self.assertTrue(res["ok"], res["reconciliation"]["mismatches"][:5])
        n = estock_synthetic.sizes(0.02)
        self.assertEqual(self.db.one("SELECT count(*) c FROM items")["c"], n["items"])
        self.assertEqual(self.db.one("SELECT count(*) c FROM customers")["c"], n["customers"])
        bal = res["plan"].checks["balance_sign"]["customers"]
        self.assertEqual(bal["statement_matches"], bal["checked"])         # balances equal their statements

    def test_sizes_are_the_pharmacy_scale(self):
        self.assertEqual(estock_synthetic.sizes()["items"], 30000)
        self.assertEqual(estock_synthetic.sizes()["sale_lines"], 52000)
