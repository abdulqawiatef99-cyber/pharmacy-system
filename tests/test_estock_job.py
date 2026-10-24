"""End-to-end migration job on synthetic E-Stock exports (layout of the real ones)."""
import json
import tempfile
from pathlib import Path

from app.dataimport import estock_job
from tests.helpers import DBTestCase
from tests.test_estock import CATALOG, stock_rows, xlsx


class TestJob(DBTestCase):
    def files(self, packs):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        (d / "c.xlsx").write_bytes(xlsx(CATALOG))
        (d / "s.xlsx").write_bytes(xlsx(stock_rows()))
        (d / "p.json").write_text(json.dumps({"packs": packs}), encoding="utf-8")
        return d

    def test_unknown_pack_is_listed_not_guessed(self):
        d = self.files({})
        rep = estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                             packs_file=d / "p.json", stores=["الصيدلية"])
        self.assertTrue(rep["stock"]["reconciliation"]["ok"])
        self.assertEqual([x["code"] for x in rep["stock"]["skipped_unknown_pack"]], ["30906"])
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 1)

    def test_reviewed_pack_file_completes_migration(self):
        d = self.files({"30906": 3})
        rep = estock_job.run(self.db, self.ctx, catalog_file=d / "c.xlsx", stock_file=d / "s.xlsx",
                             packs_file=d / "p.json", stores=["الصيدلية"])
        self.assertEqual(rep["stock"]["applied"]["stock_rows"], 2)
        self.assertEqual(rep["stock"]["applied"]["posted_base_units"], 47 + 2)
        self.assertFalse(rep["stock"].get("skipped_unknown_pack"))
