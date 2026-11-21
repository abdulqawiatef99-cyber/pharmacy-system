"""#324, the findings never checked before 19/11, each verified here with a test that failed first:
S18 (the same file uploaded twice at once), S34 (a refused new line left a blank line behind), S35 (a stale
create-and-link left an orphan item), X8 (an item editor replaced a verified composition), X9 (four-eyes
ignored a reviewer granted by exception), X14 (the purchase file endpoint served any stored file), X19 (a
tiny workbook with a million empty rows), X26 (a restored original served from the old installation).
Synthetic data only."""
import io
import re
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from app import config
from app.catalog import service as cat
from app.core import people
from app.core.errors import Conflict, DomainError, Forbidden, NotFound, ValidationFailed
from app.purchasing.importers import files
from app.purchasing.model import ExtractedInvoice, ExtractedLine
from app.purchasing.service import Purchasing
from app.safety.engine import SafetyEngine
from tests.fixtures.make_invoices import excel_invoice
from tests.helpers import DBTestCase

PW = "Staff-Pass-2026"
FUT = "12/2029"


class _Purch(DBTestCase):
    def setUp(self):
        super().setUp()
        self.settings = replace(config.load(), upload_dir=self.tmp / "uploads", anthropic_api_key=None)
        self.p = Purchasing(self.db, self.settings, ai_extract=lambda data, mime: None)
        self.sup = cat.create_supplier(self.db, self.ctx, name="ابن سينا فارما", supplier_type="distributor")
        self.pan = cat.create_item(self.db, self.ctx, {"name_en": "Panadol 500mg 24 tab", "pack_size": 2,
                                                       "public_price": 3800})

    def invoice(self, **kw):
        return excel_invoice([("PANADOL 500 MG 24 TAB", 10, 2, 40, 20, 32, 320, "P1", FUT, None)], **kw)

    def staff(self, username, role, grant=None):
        uid = people.add_user(self.db, self.ctx, username=username, full_name=username, password=PW, role_code=role)
        for g in grant or []:
            people.set_exception(self.db, self.ctx, uid, g, "grant")
        return self.login(username, PW)


# ---------------------------------------------------------------- S18
class TestS18SameFileTwiceAtOnce(_Purch):
    def test_a_second_draft_of_the_same_stored_file_is_refused_inside_the_transaction(self):
        """Two uploads both passed the duplicate check before either draft existed. The check is repeated
        under the write lock, where the second one sees the first."""
        data = self.invoice(invoice_no="")
        fid, _ = self.p._store_file(self.ctx, data, "a.xlsx", "application/vnd.ms-excel")
        ex = ExtractedInvoice(method="structured", lines=[ExtractedLine(raw_name="PANADOL 500 MG 24 TAB", qty=5)])
        self.p._create_from_extraction(self.ctx, ex, "excel", fid, None)
        with self.assertRaises(Conflict) as c:
            self.p._create_from_extraction(self.ctx, ex, "excel", fid, None)
        self.assertEqual(c.exception.code, "duplicate_file")
        self.assertEqual(self.db.one("SELECT count(*) n FROM purchase_invoices")["n"], 1)

    def test_a_cancelled_draft_frees_the_file(self):
        data = self.invoice()
        iid = self.p.import_file(self.ctx, data, "a.xlsx")
        self.p.cancel(self.ctx, iid, "رفع بالخطأ", None)
        self.assertTrue(self.p.import_file(self.ctx, data, "a.xlsx"))

    def test_manual_invoices_have_no_file_and_are_not_duplicates(self):
        self.p.create_manual(self.ctx)
        self.assertTrue(self.p.create_manual(self.ctx))


# ---------------------------------------------------------------- S34
class TestS34RefusedLineLeavesNothing(_Purch):
    def test_a_line_refused_for_its_quantity_is_not_left_blank_in_the_draft(self):
        iid = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, iid)["invoice"]["version"]
        with self.assertRaises(ValidationFailed):
            self.p.add_line(self.ctx, iid, {"item_id": self.pan, "qty": 2.5, "unit_net_price": 100}, v)
        d = self.p.get(self.ctx, iid)
        self.assertEqual((len(d["lines"]), d["invoice"]["version"]), (0, v))
        self.assertEqual(self.db.one("SELECT count(*) n FROM audit_log WHERE action='purchase.line_add'")["n"], 0)

    def test_a_good_line_is_added_whole(self):
        iid = self.p.create_manual(self.ctx, supplier_id=self.sup)
        v = self.p.get(self.ctx, iid)["invoice"]["version"]
        d = self.p.add_line(self.ctx, iid, {"item_id": self.pan, "qty": 2, "unit_net_price": 100}, v)
        self.assertEqual((d["lines"][0]["qty"], d["lines"][0]["line_total"]), (2, 200))


# ---------------------------------------------------------------- S35
class TestS35StaleCreateAndLink(_Purch):
    def draft(self):
        ex = ExtractedInvoice(method="structured", lines=[ExtractedLine(raw_name="Brand New Syrup 120ml", qty=1,
                                                                        public_price=Decimal("50"))])
        iid = self.p._create_from_extraction(self.ctx, ex, "excel", None, self.sup)
        d = self.p.get(self.ctx, iid)
        return iid, d["lines"][0]["id"], d["invoice"]["version"]

    def test_a_stale_version_creates_no_item(self):
        iid, line, v = self.draft()
        self.p.update_header(self.ctx, iid, {"notes": "edited by someone else"}, v)        # version moves on
        before = self.db.one("SELECT count(*) n FROM items")["n"]
        with self.assertRaises(Conflict) as c:
            self.p.create_item_from_line(self.ctx, iid, line, {"pack_size": 1}, v)
        self.assertEqual(c.exception.code, "stale_version")
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], before)

    def test_without_the_right_to_edit_the_invoice_no_item_is_created(self):
        iid, line, v = self.draft()
        uid = people.add_user(self.db, self.ctx, username="cataloger", full_name="cataloger", password=PW, role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "items.write", "grant")
        people.set_exception(self.db, self.ctx, uid, "purchases.edit", "deny")
        sctx = self.login("cataloger", PW)
        before = self.db.one("SELECT count(*) n FROM items")["n"]
        with self.assertRaises(Forbidden):
            self.p.create_item_from_line(sctx, iid, line, {"pack_size": 1}, v)
        self.assertEqual(self.db.one("SELECT count(*) n FROM items")["n"], before)

    def test_the_current_version_creates_and_links(self):
        iid, line, v = self.draft()
        d = self.p.create_item_from_line(self.ctx, iid, line, {"pack_size": 1}, v)
        self.assertEqual(d["lines"][0]["match_status"], "confirmed")


# ---------------------------------------------------------------- X14 / X26
class TestX14X26PurchaseFiles(_Purch):
    def foreign_file(self, data=b"\x89PNG\r\n\x1a\n proof of a transfer"):
        """A stored file that is not a purchase invoice's original (the way a transfer proof is stored)."""
        from app.db import new_id
        folder = self.settings.upload_dir / self.ctx.org_id / "transfers"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "proof.png").write_bytes(data)
        fid = new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO source_files (id, org_id, sha256, filename, mime_type, size_bytes, storage_path,"
                            " uploaded_by) VALUES (?,?,?,?,?,?,?,?)",
                            (fid, self.ctx.org_id, "f" * 64, "proof.png", "image/png", len(data),
                             str(folder / "proof.png"), self.ctx.user_id))
        return fid

    def test_a_file_that_is_no_purchase_original_is_not_served(self):
        fid = self.foreign_file()
        with self.assertRaises(NotFound):
            self.p.get_file(self.ctx, fid)

    def test_the_original_of_an_invoice_is_served(self):
        data = self.invoice()
        iid = self.p.import_file(self.ctx, data, "a.xlsx")
        fid = self.p.get(self.ctx, iid)["invoice"]["source_file_id"]
        self.assertEqual(self.p.get_file(self.ctx, fid)[0], data)

    def test_another_pharmacys_original_is_not_served(self):
        iid = self.p.import_file(self.ctx, self.invoice(), "a.xlsx")
        fid = self.p.get(self.ctx, iid)["invoice"]["source_file_id"]
        _, other = self.second_tenant()
        with self.assertRaises(NotFound):
            self.p.get_file(other, fid)

    def test_the_file_is_read_from_this_installation_only(self):
        """A restore into another folder on the same PC: the registered absolute path still exists in the old
        installation. Serving it made the restore drill pass with the restored copy missing."""
        data = self.invoice()
        iid = self.p.import_file(self.ctx, data, "a.xlsx")
        fid = self.p.get(self.ctx, iid)["invoice"]["source_file_id"]
        restored = Purchasing(self.db, replace(self.settings, upload_dir=self.tmp / "restored-uploads"),
                              ai_extract=lambda d, m: None)
        with self.assertRaises(NotFound):
            restored.get_file(self.ctx, fid)                      # not copied yet: missing, not the old one
        old = Path(self.db.one("SELECT storage_path FROM source_files WHERE id=?", (fid,))["storage_path"])
        new = self.tmp / "restored-uploads" / self.ctx.org_id / old.name
        new.parent.mkdir(parents=True)
        new.write_bytes(b"restored copy")
        self.assertEqual(restored.get_file(self.ctx, fid)[0], b"restored copy")


# ---------------------------------------------------------------- X19
class TestX19SparseWorkbook(DBTestCase):
    @staticmethod
    def sparse(last_row: int) -> bytes:
        """Two filled cells, `last_row` rows apart, without the sheet's declared dimension."""
        wb = Workbook()
        wb.active["A1"], wb.active[f"A{last_row}"] = "h", "x"
        raw = io.BytesIO()
        wb.save(raw)
        out = io.BytesIO()
        with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in zin.infolist():
                d = zin.read(i.filename)
                if i.filename.endswith("sheet1.xml"):
                    d = re.sub(rb"<dimension[^>]*/>", b"", d)
                zout.writestr(i, d)
        return out.getvalue()

    def test_a_small_file_with_a_huge_gap_is_refused(self):
        data = self.sparse(files.MAX_XLSX_EMPTY_ROWS + 5000)
        self.assertLess(len(data), 20_000)
        with self.assertRaises(ValidationFailed) as c:
            files.read_excel_sheets(data)
        self.assertEqual(c.exception.code, "too_large")

    def test_a_few_blank_rows_are_fine_and_dropped(self):
        rows = files.read_excel_sheets(self.sparse(500))[0][1]
        self.assertEqual(rows, [["h"], ["x"]])

    def test_empty_rows_spend_the_cell_budget(self):
        from unittest import mock
        with mock.patch.object(files, "MAX_XLSX_CELLS", 100):
            with self.assertRaises(ValidationFailed):
                files.read_excel_sheets(self.sparse(500))


# ---------------------------------------------------------------- X8 / X9
class TestX8VerifiedComposition(DBTestCase):
    def setUp(self):
        super().setUp()
        self.se = SafetyEngine(self.db)
        mk = lambda n: cat.create_item(self.db, self.ctx, {"name_en": n}, force=True)   # noqa: E731
        self.a, self.b = mk("Testium 500mg 20 tab"), mk("Testium Plus 20 tab")
        for i in (self.a, self.b):
            self.se.set_item_ingredients(self.ctx, i, [("Testium", "500mg")])
            self.se.verify_item_ingredients(self.ctx, i)
        people.add_user(self.db, self.ctx, username="pharm1", full_name="pharm1", password=PW, role_code="pharmacist")
        self.ph = self.login("pharm1", PW)

    def dup(self, ctx):
        return [f.code for f in self.se.check(ctx, [{"item_id": self.a}, {"item_id": self.b}])]

    def test_an_item_editor_cannot_replace_a_verified_composition(self):
        self.assertIn("duplicate_ingredient", self.dup(self.ctx))
        with self.assertRaises(Forbidden):
            self.se.set_item_ingredients(self.ph, self.a, [("Mockazole", "10mg")])
        self.assertIn("duplicate_ingredient", self.dup(self.ctx))            # the check is still armed
        self.assertEqual(self.db.one("SELECT status FROM item_ingredients WHERE item_id=?", (self.a,))["status"], "verified")

    def test_an_item_editor_still_enters_a_first_or_unverified_composition(self):
        c = cat.create_item(self.db, self.ctx, {"name_en": "Unmapped Syrup 100ml"}, force=True)
        self.se.set_item_ingredients(self.ph, c, [("Testium", "100mg")])
        self.se.set_item_ingredients(self.ph, c, [("Mockazole", "10mg")])     # still unverified: free to correct
        self.assertEqual(self.db.one("SELECT count(*) n FROM item_ingredients WHERE item_id=?", (c,))["n"], 1)

    def test_who_may_verify_may_replace(self):
        self.se.set_item_ingredients(self.ctx, self.a, [("Mockazole", "10mg")])
        self.assertEqual(self.db.one("SELECT status FROM item_ingredients WHERE item_id=?", (self.a,))["status"], "unverified")


class TestX9FourEyesCountsGrantedReviewers(DBTestCase):
    def setUp(self):
        super().setUp()
        self.se = SafetyEngine(self.db)
        src = self.se.add_source(self.ctx, name="FICTIONAL TEST SOURCE", version="0", license="test only")
        it = cat.create_item(self.db, self.ctx, {"name_en": "Testium 500mg 20 tab"}, force=True)
        self.rule = self.se.create_rule(self.ctx, rule_type="max_qty", severity="high", title="حد تجربة",
                                        explanation="قاعدة تجربة", params={"item_id": it, "max_base_units": 2},
                                        source_id=src)

    def test_a_pharmacist_granted_safety_approve_is_a_second_reviewer(self):
        uid = people.add_user(self.db, self.ctx, username="pharm1", full_name="pharm1", password=PW, role_code="pharmacist")
        people.set_exception(self.db, self.ctx, uid, "safety.approve", "grant")
        with self.assertRaises(Forbidden) as c:
            self.se.approve_rule(self.ctx, self.rule, self_approval_reason="راجعها دكتور تجربة بنفسه")
        self.assertEqual(c.exception.code, "four_eyes")
        self.se.approve_rule(self.login("pharm1", PW), self.rule)

    def test_a_reviewer_whose_right_was_denied_does_not_block_the_only_one_left(self):
        uid = people.add_user(self.db, self.ctx, username="pharm1", full_name="pharm1", password=PW, role_code="pharmacist")
        people.set_exception(self.db, self.ctx, uid, "safety.approve", "grant")
        people.set_exception(self.db, self.ctx, uid, "safety.approve", "deny")
        self.se.approve_rule(self.ctx, self.rule, self_approval_reason="راجعها دكتور تجربة بنفسه")

    def test_alone_the_owner_still_needs_a_written_reason(self):
        with self.assertRaises(DomainError) as c:
            self.se.approve_rule(self.ctx, self.rule)
        self.assertEqual(c.exception.code, "self_approval_reason_required")


if __name__ == "__main__":
    import unittest
    unittest.main()
