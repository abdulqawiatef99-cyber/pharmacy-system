"""Reference catalog stages M2 (linking), M3 (price proposals) and M4 (supplier files). Synthetic data."""
from __future__ import annotations

import io

from openpyxl import Workbook

from app.catalog import reference as ref
from app.catalog import reffile, reflink, refprices
from app.catalog import service as cat
from app.core.errors import Conflict, Forbidden, ValidationFailed
from app.core import people
from tests.helpers import DBTestCase
from tests.test_reference_catalog import drug, page


def links(db, org, item_id=None):
    sql = "SELECT * FROM ref_links WHERE org_id=?" + (" AND item_id=?" if item_id else "")
    return [dict(r) for r in db.all(sql, (org,) + ((item_id,) if item_id else ()))]


class Base(DBTestCase):
    def setUp(self):
        super().setUp()
        self.org_id = self.ctx.org_id
        ref.import_pages(self.db, [page(
            drug("panadol-500-24", "Panadol 500mg 24 tabs", price=40, barcode="6221111111111", units=24),
            drug("panadol-500-48", "Panadol 500mg 48 tabs", price=75, units=48),
            drug("panadol-extra-24", "Panadol Extra 24 tabs", price=55, units=24),
            drug("omega3", "Omega Plus 3 1000mg 30 caps", ings=(("Omega-3", "1000 mg"),), strength="1000 mg",
                 form="capsule", units=30, price=200),
            drug("omega6", "Omega Plus 6 1000mg 30 caps", ings=(("Omega-6", "1000 mg"),), strength="1000 mg",
                 form="capsule", units=30, price=180),
            drug("amoxil-250-susp", "Amoxil 250mg/5ml susp 100ml", ings=(("Amoxicillin", "250 mg"),),
                 strength="250 mg/5 ml", form="suspension", units=None, price=30),
            drug("amoxil-500-caps", "Amoxil 500mg 16 caps", ings=(("Amoxicillin", "500 mg"),), strength="500 mg",
                 form="capsule", units=16, price=45),
        )], observed_at="2026-10-30T10:00:00Z")

    def item(self, name, price=None, barcode=None, **kw):
        return cat.create_item(self.db, self.ctx, {"name_en": name, "public_price": price, **kw},
                               barcodes=[barcode] if barcode else None)

    def ref_id(self, sid):
        return self.db.one("SELECT id FROM ref_products WHERE source_id=?", (sid,))["id"]


class LinkTests(Base):
    def test_barcode_with_agreeing_name_is_strong(self):
        i = self.item("Panadol 500mg 24 tab", 3800, barcode="6221111111111")
        reflink.suggest(self.db, self.ctx)
        ls = links(self.db, self.org_id, i)
        top = max(ls, key=lambda l: l["score"])
        self.assertEqual((top["ref_id"], top["method"], top["strength"], top["status"]),
                         (self.ref_id("panadol-500-24"), "barcode", "strong", "suggested"))

    def test_barcode_but_different_product_is_not_strong(self):
        i = self.item("Brufen 400mg 30 tabs", barcode="6221111111111")
        reflink.suggest(self.db, self.ctx)
        self.assertTrue(all(l["strength"] == "weak" for l in links(self.db, self.org_id, i)))

    def test_close_names_never_strong(self):
        o3 = self.item("Omega Plus 3 1000mg 30 caps")
        p48 = self.item("Panadol 500mg 48 tabs")
        extra = self.item("Panadol Extra 24 tabs")
        amx = self.item("Amoxil 500mg 16 caps")
        reflink.suggest(self.db, self.ctx)
        strong = {(l["item_id"], l["ref_id"]) for l in links(self.db, self.org_id) if l["strength"] == "strong"}
        self.assertIn((o3, self.ref_id("omega3")), strong)
        self.assertNotIn((o3, self.ref_id("omega6")), strong)
        self.assertIn((p48, self.ref_id("panadol-500-48")), strong)
        self.assertNotIn((p48, self.ref_id("panadol-500-24")), strong)            # different pack
        self.assertNotIn((extra, self.ref_id("panadol-500-24")), strong)          # modifier Extra
        self.assertNotIn((amx, self.ref_id("amoxil-250-susp")), strong)           # strength + form

    def test_approve_reject_and_bulk(self):
        a = self.item("Panadol 500mg 48 tabs")
        b = self.item("Omega Plus 3 1000mg 30 caps")
        reflink.suggest(self.db, self.ctx)
        r = reflink.approve_all_strong(self.db, self.ctx)
        self.assertEqual(r["approved"], 2)
        appr = {(l["item_id"], l["ref_id"]) for l in links(self.db, self.org_id) if l["status"] == "approved"}
        self.assertEqual(appr, {(a, self.ref_id("panadol-500-48")), (b, self.ref_id("omega3"))})
        # a second approved link from the same source is refused
        with self.db.transaction():
            self.db.execute("INSERT INTO ref_links (org_id, item_id, ref_id, source, method, score, strength)"
                            " VALUES (?,?,?,?,?,?,?)", (self.org_id, a, self.ref_id("panadol-500-24"), "dwaprice",
                                                        "manual", 0.5, "weak"))
        with self.assertRaises(Conflict):
            reflink.decide(self.db, self.ctx, a, self.ref_id("panadol-500-24"), "approve")
        # rejected stays rejected on the next run
        reflink.decide(self.db, self.ctx, a, self.ref_id("panadol-500-24"), "reject")
        reflink.unlink(self.db, self.ctx, a, self.ref_id("panadol-500-48"))
        reflink.suggest(self.db, self.ctx)
        st = {l["ref_id"]: l["status"] for l in links(self.db, self.org_id, a)}
        self.assertEqual(st.get(self.ref_id("panadol-500-24")), "rejected")

    def test_approve_does_not_touch_item_unless_asked(self):
        i = self.item("Omega Plus 3 1000mg 30 caps", 19000)
        before = dict(self.db.one("SELECT * FROM items WHERE id=?", (i,)))
        reflink.suggest(self.db, self.ctx)
        reflink.decide(self.db, self.ctx, i, self.ref_id("omega3"), "approve")
        self.assertEqual(dict(self.db.one("SELECT * FROM items WHERE id=?", (i,))), before)

    def test_fill_empty_details_only(self):
        i = self.item("Omega Plus 3 1000mg 30 caps", strength="1000 mg")
        reflink.suggest(self.db, self.ctx)
        r = reflink.decide(self.db, self.ctx, i, self.ref_id("omega3"), "approve", fill_empty_details=True)
        self.assertEqual(r["filled"].get("active_ingredient"), "Omega-3")
        self.assertNotIn("strength", r["filled"])                                 # already had one
        self.assertEqual(cat.get_item(self.db, self.ctx, i)["active_ingredient"], "Omega-3")

    def test_tenants_are_isolated(self):
        i = self.item("Panadol 500mg 48 tabs")
        reflink.suggest(self.db, self.ctx)
        org2, ctx2 = self.second_tenant()
        self.assertEqual(reflink.queue(self.db, ctx2)["total"], 0)
        with self.assertRaises(Exception):
            reflink.decide(self.db, ctx2, i, self.ref_id("panadol-500-48"), "approve")


class PriceTests(Base):
    def linked(self, name, price, sid):
        i = self.item(name, price)
        reflink.suggest(self.db, self.ctx, item_ids=[i])
        reflink.decide(self.db, self.ctx, i, self.ref_id(sid), "approve")
        return i

    def price(self, i):
        return self.db.one("SELECT public_price FROM items WHERE id=?", (i,))["public_price"]

    def test_propose_apply_rollback(self):
        i = self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        same = self.linked("Omega Plus 3 1000mg 30 caps", 20000, "omega3")
        r = refprices.propose(self.db, self.ctx)
        self.assertEqual((r["proposed"], r["same_price"]), (1, 1))
        self.assertEqual(self.price(i), 7000)                                      # nothing changes by itself
        pend = refprices.pending(self.db, self.ctx)["rows"]
        self.assertEqual((pend[0]["old_price"], pend[0]["new_price"]), (7000, 7500))
        self.assertEqual(refprices.propose(self.db, self.ctx)["proposed"], 0)      # idempotent
        a = refprices.apply(self.db, self.ctx, [pend[0]["id"]])
        self.assertEqual((a["applied"], self.price(i)), (1, 7500))
        h = self.db.one("SELECT source, previous_price FROM item_price_history WHERE item_id=? ORDER BY observed_at DESC"
                        " LIMIT 1", (i,))
        self.assertEqual((h["source"], h["previous_price"]), ("reference", 7000))
        rb = refprices.rollback(self.db, self.ctx, a["batch_id"])
        self.assertEqual((rb["restored"], self.price(i)), (1, 7000))
        with self.assertRaises(ValidationFailed):
            refprices.rollback(self.db, self.ctx, a["batch_id"])
        self.assertEqual(self.price(same), 20000)

    def test_price_changed_meanwhile_is_skipped(self):
        i = self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        refprices.propose(self.db, self.ctx)
        pid = refprices.pending(self.db, self.ctx)["rows"][0]["id"]
        cat.update_item(self.db, self.ctx, i, {"public_price": 7200})
        a = refprices.apply(self.db, self.ctx, [pid])
        self.assertEqual((a["applied"], a["skipped_changed"], self.price(i)), (0, 1, 7200))

    def test_rollback_does_not_undo_a_later_manual_change(self):
        i = self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        refprices.propose(self.db, self.ctx)
        a = refprices.apply(self.db, self.ctx, [refprices.pending(self.db, self.ctx)["rows"][0]["id"]])
        cat.update_item(self.db, self.ctx, i, {"public_price": 7700})
        rb = refprices.rollback(self.db, self.ctx, a["batch_id"])
        self.assertEqual((rb["restored"], rb["skipped_changed_again"], self.price(i)), (0, 1, 7700))

    def test_rejected_proposal_is_not_repeated(self):
        self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        refprices.propose(self.db, self.ctx)
        refprices.reject(self.db, self.ctx, [refprices.pending(self.db, self.ctx)["rows"][0]["id"]])
        self.assertEqual(refprices.propose(self.db, self.ctx)["proposed"], 0)
        # a NEW reference price is proposed again
        ref.import_pages(self.db, [page(drug("panadol-500-48", "Panadol 500mg 48 tabs", price=80, eff="2026-11-01",
                                             units=48))])
        self.assertEqual(refprices.propose(self.db, self.ctx)["proposed"], 1)

    def test_sources_that_disagree_are_a_conflict(self):
        i = self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        wb = Workbook()
        ws = wb.active
        ws.append(["الكود", "اسم الصنف", "سعر الجمهور"])
        ws.append(["P48", "Panadol 500mg 48 tabs", 79])
        buf = io.BytesIO()
        wb.save(buf)
        reffile.import_file(self.db, buf.getvalue(), "list.xlsx", "Ibn Sina")
        reflink.suggest(self.db, self.ctx, item_ids=[i], source="file:ibn-sina")
        fid = self.db.one("SELECT id FROM ref_products WHERE source='file:ibn-sina'")["id"]
        reflink.decide(self.db, self.ctx, i, fid, "approve")
        r = refprices.propose(self.db, self.ctx)
        self.assertEqual(r["conflicts"], 1)
        pid = refprices.pending(self.db, self.ctx)["rows"][0]["id"]
        self.assertEqual(refprices.apply(self.db, self.ctx, [pid])["skipped_conflict"], 1)
        self.assertEqual(refprices.apply(self.db, self.ctx, [pid], accept_conflicts=True)["applied"], 1)

    def test_price_permission_required(self):
        uid = people.add_user(self.db, self.ctx, username="emp", full_name="موظف", password="Emp-Pass-2026",
                              role_code="assistant")
        people.set_exception(self.db, self.ctx, uid, "items.price", "deny")
        ectx = self.login("emp", "Emp-Pass-2026")
        with self.assertRaises(Forbidden):
            refprices.propose(self.db, ectx)

    def test_stale_report(self):
        self.linked("Panadol 500mg 48 tabs", 7000, "panadol-500-48")
        st = refprices.stale(self.db, self.ctx)
        self.assertEqual(len(st["item_price_older_than_reference"]), 0)   # item price set today, ref is 2024
        ref.import_pages(self.db, [page(drug("panadol-500-48", "Panadol 500mg 48 tabs", price=80, eff="2099-01-01",
                                             units=48))])
        self.assertEqual(len(refprices.stale(self.db, self.ctx)["item_price_older_than_reference"]), 1)


class FileSourceTests(DBTestCase):
    def xlsx(self, rows):
        wb = Workbook()
        ws = wb.active
        ws.append(["تقرير أسعار"])                       # junk line above the header
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_supplier_file_with_non_drugs(self):
        data = self.xlsx([["الكود", "اسم الصنف", "الباركود", "سعر الجمهور", "الشركه"],
                          ["C1", "Vichy Mineral 89 50ml", "3337875543248", "1250.50", "L'Oreal"],
                          ["C2", "Panadol 500mg 24 tabs", "", "40", "GSK"],
                          ["C3", "Mystery item", "", "abc", ""],
                          ["C1", "Duplicate code", "", "10", ""],
                          ["", "", "", "", ""]])
        r = reffile.import_file(self.db, data, "ibn-sina.xlsx", "Ibn Sina")
        self.assertEqual((r["source"], r["created"], r["prices_added"]), ("file:ibn-sina", 2, 2))
        self.assertEqual(len(r["rejected"]), 2)
        v = self.db.one("SELECT * FROM ref_products WHERE source='file:ibn-sina' AND source_id='C1'")
        self.assertIsNone(v["ingredient_key"])                          # cosmetic: no pharma fields needed
        self.assertEqual(self.db.one("SELECT price FROM ref_prices WHERE ref_id=?", (v["id"],))["price"], 125050)
        again = reffile.import_file(self.db, data, "ibn-sina.xlsx", "Ibn Sina")
        self.assertEqual((again["created"], again["unchanged"], again["prices_added"]), (0, 2, 0))

    def test_csv_and_bad_files(self):
        csv = "name,barcode,price\nCetal 500mg 20 tabs,6224000000011,25\n".encode()
        self.assertEqual(reffile.import_file(self.db, csv, "x.csv", "Pharma Overseas")["created"], 1)
        with self.assertRaises(ValidationFailed):
            reffile.import_file(self.db, b"a,b\n1,2\n", "y.csv", "Other")
        with self.assertRaises(ValidationFailed):
            reffile.source_name("  ")


from tests.test_api import APICase, H  # noqa: E402


class ReferenceAPITests(APICase):
    def test_owner_flow_over_http(self):
        self.http_login()
        wb = Workbook()
        ws = wb.active
        ws.append(["الكود", "اسم الصنف", "سعر الجمهور"])
        ws.append(["P24", "Panadol 500mg 24 tab", 45])
        buf = io.BytesIO()
        wb.save(buf)
        r = self.c.post("/api/reference/import-file", headers=H, data={"label": "Ibn Sina"},
                        files={"file": ("list.xlsx", buf.getvalue(), "application/octet-stream")})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["created"], 1)
        self.assertEqual(self.c.post("/api/reference/links/suggest", headers=H, json={}).status_code, 200)
        q = self.c.get("/api/reference/links?strength=strong", headers=H).json()
        self.assertEqual(q["total"], 1)
        row = q["rows"][0]
        self.assertEqual(row["ref_price"], 4500)
        d = self.c.post("/api/reference/links/decide", headers=H,
                        json={"item_id": row["item_id"], "ref_id": row["ref_id"], "decision": "approve"})
        self.assertEqual(d.status_code, 200, d.text)
        self.assertEqual(self.c.post("/api/reference/prices/propose", headers=H, json={}).json()["proposed"], 1)
        pid = self.c.get("/api/reference/prices/pending", headers=H).json()["rows"][0]["id"]
        a = self.c.post("/api/reference/prices/apply", headers=H, json={"ids": [pid]}).json()
        self.assertEqual(a["applied"], 1)
        self.assertEqual(self.db.one("SELECT public_price FROM items WHERE id=?", (self.pan,))["public_price"], 4500)
        rb = self.c.post(f"/api/reference/prices/batches/{a['batch_id']}/rollback", headers=H, json={}).json()
        self.assertEqual(rb["restored"], 1)
        self.assertEqual(self.db.one("SELECT public_price FROM items WHERE id=?", (self.pan,))["public_price"], 4000)
        s = self.c.get("/api/reference/summary", headers=H).json()
        self.assertEqual(s["sources"]["file:ibn-sina"]["products"], 1)
        self.assertEqual(s["coverage"]["linked"], 1)
