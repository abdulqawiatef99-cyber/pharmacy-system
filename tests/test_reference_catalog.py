"""External reference catalog (stage M1, docs/CATALOG_DATA_PLAN.md).

Records follow the published Dwaprice v1 Drug shape. Except the Congestal record (the documented
example), they are SYNTHETIC test data, not real prices.
"""
from __future__ import annotations

import copy
import unittest

from app.catalog import reference as ref
from app.catalog import service as cat
from app.catalog.normalize import ingredient_key
from tests.helpers import DBTestCase


def drug(sid, name, *, price=50, eff="2024-06-06", barcode=None, ings=(("Paracetamol", "500 mg"),),
         strength="500 mg", form="tablet", units=20, status="listed"):
    return {
        "id": sid, "trade_name_en": name, "trade_name_ar": None, "scientific_name": "+".join(i[0] for i in ings),
        "ingredients": [{"id": n.lower(), "name_en": n, "name_ar": None, "strength": s, "atc_code": None}
                        for n, s in ings],
        "strength": strength, "dosage_form": {"class": form, "en": form.title(), "ar": None},
        "route": "oral", "route_text": "Oral", "units_per_package": units, "packaging": None,
        "price": {"current": price, "previous": None, "unit_price": None, "currency": "EGP",
                  "effective_date": eff, "updated_at": "2026-10-21T00:00:00Z", "verified": price is not None,
                  "verified_at": None},
        "barcode": barcode, "company": {"id": "x", "name": "Test Co"}, "manufacturer": None, "category": None,
        "atc_codes": [], "prescription_status": "otc", "status": status, "price_list_name": name,
        "created_at": "2026-10-21T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z",
        "links": {"web_url": "https://dwaprice.com/dawa/" + sid},
    }


CONGESTAL = drug("congestal-20-tabs", "Congestal 20 tabs", price=50, barcode="6221051011317",
                 ings=(("Paracetamol (acetaminophen)", "650 mg"), ("Chlorphenamine maleate", "4 mg"),
                       ("Pseudoephedrine hydrochloride", "60 mg")), strength="650/4/60 mg")


def page(*drugs, cursor=None, version="2026-10-21.1"):
    return {"data": list(drugs), "page": {"limit": 100, "has_more": bool(cursor), "next_cursor": cursor},
            "meta": {"dataset_version": version, "generated_at": "2026-10-21T14:36:16Z"}}


class ReferenceImportTests(DBTestCase):
    def count(self, table):
        return self.db.one(f"SELECT count(*) n FROM {table}")["n"]

    def test_reimport_is_idempotent(self):
        r1 = ref.import_pages(self.db, [page(CONGESTAL)], observed_at="2026-10-30T10:00:00Z")
        r2 = ref.import_pages(self.db, [page(copy.deepcopy(CONGESTAL))], observed_at="2026-10-30T11:00:00Z")
        self.assertEqual((r1["created"], r1["prices_added"]), (1, 1))
        self.assertEqual((r2["created"], r2["updated"], r2["unchanged"], r2["prices_added"]), (0, 0, 1, 0))
        self.assertEqual((self.count("ref_products"), self.count("ref_prices"), self.count("ref_barcodes")), (1, 1, 1))
        self.assertEqual(self.count("ref_ingredients"), 3)
        self.assertEqual(ref.sync_state(self.db)["dataset_version"], "2026-10-21.1")

    def test_price_change_keeps_history_in_piasters(self):
        ref.import_pages(self.db, [page(drug("a", "Panadol 500mg 20 tabs", price=31, eff="2023-01-01"))])
        r = ref.import_pages(self.db, [page(drug("a", "Panadol 500mg 20 tabs", price=50.25, eff="2024-06-06"))])
        self.assertEqual((r["updated"], r["prices_added"]), (1, 1))
        self.assertEqual([h["price"] for h in ref.price_history(self.db, "a")], [3100, 5025])

    def test_missing_price_is_not_zero(self):
        r = ref.import_pages(self.db, [page(drug("np", "No Price 10 tabs", price=None))])
        self.assertEqual((r["created"], r["no_price"], r["prices_added"]), (1, 1, 0))
        self.assertEqual(self.count("ref_prices"), 0)
        self.assertEqual(ref.summary(self.db)["with_price"], 0)

    def test_shared_barcode_keeps_both_packs(self):
        ref.import_pages(self.db, [page(drug("p10", "Augmentin 1g 14 tabs", barcode="6221000000017", units=14),
                                        drug("p20", "Augmentin 1g 20 tabs", barcode="6221000000017", units=20))])
        found = ref.by_barcode(self.db, "6221000000017")
        self.assertEqual(sorted(f["source_id"] for f in found), ["p10", "p20"])
        self.assertEqual(ref.summary(self.db)["shared_barcodes"], 1)

    def test_bad_records_are_reported_not_fatal(self):
        r = ref.import_pages(self.db, [page({"trade_name_en": "no id"}, drug("ok", "Fine 10 tabs"),
                                            drug("neg", "Negative 10 tabs", price=-5), "junk")])
        self.assertEqual(r["created"], 1)
        self.assertEqual(len(r["rejected"]), 3)

    def test_pharmacy_items_are_never_touched(self):
        iid = cat.create_item(self.db, self.ctx, {"name_en": "Congestal 20 tabs", "public_price": 3100})
        before = dict(self.db.one("SELECT * FROM items WHERE id=?", (iid,)))
        ref.import_pages(self.db, [page(CONGESTAL)])
        ref.apply_events(self.db, [{"id": 1, "type": "price_changed", "drug_id": CONGESTAL["id"],
                                    "detected_at": "2026-10-30T12:00:00Z", "data": {}, "drug": CONGESTAL}])
        self.assertEqual(dict(self.db.one("SELECT * FROM items WHERE id=?", (iid,))), before)


class ReferenceEventTests(DBTestCase):
    def test_events_apply_once_and_track_position(self):
        ref.import_pages(self.db, [page(drug("a", "Brand A 500mg 20 tabs", price=40))])
        newer = drug("a", "Brand A 500mg 20 tabs", price=60, eff="2026-10-30")
        ev = {"id": 7, "type": "price_changed", "drug_id": "a", "detected_at": "2026-10-30T12:00:00Z",
              "data": {"old": 40, "new": 60}, "drug": newer}
        r = ref.apply_events(self.db, [ev, copy.deepcopy(ev)])
        self.assertEqual((r["applied"], r["skipped_duplicate"]), (1, 1))
        r2 = ref.apply_events(self.db, [ev])
        self.assertEqual((r2["applied"], r2["skipped_duplicate"]), (0, 1))
        self.assertEqual([h["price"] for h in ref.price_history(self.db, "a")], [4000, 6000])
        self.assertEqual(ref.sync_state(self.db)["latest_detected_at"], "2026-10-30T12:00:00Z")

    def test_removed_and_renamed(self):
        ref.import_pages(self.db, [page(drug("old", "Old Name 10 tabs"), drug("gone", "Gone 10 tabs"))])
        ref.apply_events(self.db, [
            {"id": 1, "type": "removed", "drug_id": "gone", "detected_at": "2026-10-30T01:00:00Z",
             "data": {"last_price": 50, "last_status": "cancelled"}, "drug": None},
            {"id": 2, "type": "renamed", "drug_id": "old", "detected_at": "2026-10-30T02:00:00Z",
             "data": {"new_id": "new"}, "drug": drug("new", "New Name 10 tabs")},
        ])
        rows = {r["source_id"]: r for r in self.db.all("SELECT * FROM ref_products")}
        self.assertIsNotNone(rows["gone"]["removed_at"])
        self.assertEqual(rows["gone"]["listing_status"], "cancelled")
        self.assertEqual(rows["old"]["replaced_by"], "new")
        self.assertIn("new", rows)
        self.assertEqual(ref.summary(self.db)["removed"], 1)

    def test_unknown_event_is_rejected_and_not_marked_applied(self):
        r = ref.apply_events(self.db, [{"id": 9, "type": "mystery", "drug_id": "x", "drug": None}])
        self.assertEqual((r["applied"], len(r["rejected"])), (0, 1))
        self.assertIsNone(self.db.one("SELECT 1 FROM ref_events_applied WHERE event_id=9"))


class NegativeIdentityTests(DBTestCase):
    """Different products must stay different, now and when M2 links them to pharmacy items."""

    def test_ingredient_keys_keep_close_names_apart(self):
        for a, b in (("Omega-3", "Omega-6"), ("Omega 3 fatty acids", "Omega 6 fatty acids"),
                     ("أوميجا 3", "أوميجا 6"), ("Vitamin B6", "Vitamin B12"), ("PEG-3350", "PEG-4000")):
            self.assertNotEqual(ingredient_key(a), ingredient_key(b), (a, b))
        self.assertEqual(ingredient_key("Amoxicillin 875mg + Clavulanic Acid 125mg"),
                         ingredient_key("clavulanic acid / amoxicillin"))

    def test_presentations_stay_separate_records(self):
        ref.import_pages(self.db, [page(
            drug("amx-250-susp", "Amoxil 250mg/5ml susp", ings=(("Amoxicillin", "250 mg"),), strength="250 mg/5 ml",
                 form="suspension", units=None),
            drug("amx-500-cap", "Amoxil 500mg 16 caps", ings=(("Amoxicillin", "500 mg"),), strength="500 mg",
                 form="capsule", units=16),
            drug("amx-500-cap-24", "Amoxil 500mg 24 caps", ings=(("Amoxicillin", "500 mg"),), strength="500 mg",
                 form="capsule", units=24),
            drug("o3", "Omega Plus 3 1000mg", ings=(("Omega-3", "1000 mg"),), strength="1000 mg", form="capsule"),
            drug("o6", "Omega Plus 6 1000mg", ings=(("Omega-6", "1000 mg"),), strength="1000 mg", form="capsule"),
        )])
        rows = {r["source_id"]: r for r in self.db.all("SELECT * FROM ref_products")}
        self.assertEqual(len(rows), 5)
        self.assertNotEqual(rows["o3"]["ingredient_key"], rows["o6"]["ingredient_key"])
        self.assertNotEqual(rows["amx-250-susp"]["strength_norm"], rows["amx-500-cap"]["strength_norm"])
        self.assertNotEqual(rows["amx-500-cap"]["units_per_package"], rows["amx-500-cap-24"]["units_per_package"])

    def test_alternatives_no_longer_offer_omega6_for_omega3(self):
        from app.catalog import alternatives as alts
        o3 = cat.create_item(self.db, self.ctx, {"name_en": "Omega Plus 3 1000mg 30 caps",
                                                 "active_ingredient": "Omega-3", "strength": "1000 mg"})
        cat.create_item(self.db, self.ctx, {"name_en": "Omega Plus 6 1000mg 30 caps",
                                            "active_ingredient": "Omega-6", "strength": "1000 mg"})
        res = alts.for_item(self.db, self.ctx, o3)
        names = [x["name_en"] for k in ("similar", "other_strength", "alternatives") for x in res[k]]
        self.assertFalse(any("Omega Plus 6" in n for n in names), res)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def test_sample_mode_sends_no_key_and_follows_cursor(self):
        s = FakeSession([FakeResponse(200, page(CONGESTAL, cursor="c2")), FakeResponse(200, page(drug("b", "B")))])
        c = ref.DwapriceClient(sample=True, api_key="dp_live_SHOULD_NOT_BE_SENT", session=s, sleep=lambda x: None)
        pages = list(c.catalog(max_pages=5))
        self.assertEqual(len(pages), 2)
        self.assertTrue(all("Authorization" not in x["headers"] for x in s.calls))
        self.assertTrue(s.calls[0]["url"].endswith("/sample/drugs"))
        self.assertEqual(s.calls[1]["params"]["cursor"], "c2")

    def test_rate_limit_waits_then_retries(self):
        waits = []
        s = FakeSession([FakeResponse(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "3"}),
                         FakeResponse(200, page(CONGESTAL))])
        c = ref.DwapriceClient(api_key="dp_live_x", session=s, sleep=waits.append)
        self.assertEqual(len(list(c.catalog(max_pages=1))), 1)
        self.assertEqual(waits, [3.0])
        self.assertEqual(s.calls[0]["headers"]["Authorization"], "Bearer dp_live_x")

    def test_quota_exceeded_stops_instead_of_hanging(self):
        s = FakeSession([FakeResponse(429, {"error": {"code": "quota_exceeded", "message": "monthly"}},
                                      {"Retry-After": "900000"})])
        c = ref.DwapriceClient(api_key="dp_live_x", session=s, sleep=lambda x: self.fail("must not sleep"))
        with self.assertRaises(ref.DwapriceError):
            list(c.catalog(max_pages=1))

    def test_paid_mode_requires_key(self):
        with self.assertRaises(ref.DwapriceError):
            ref.DwapriceClient(sample=False, api_key=None, session=FakeSession([]))


if __name__ == "__main__":
    unittest.main()


class BundleTests(DBTestCase):
    def test_bundle_round_trip_is_idempotent_and_checked(self):
        import zipfile
        from app.db import Database
        ref.import_pages(self.db, [page(CONGESTAL, drug("np", "No price 10 tabs", price=None))])
        ref.import_pages(self.db, [page(drug("np", "No price 10 tabs", price=12))])
        path = self.tmp / "ref.zip"
        man = ref.export_bundle(self.db, path)
        self.assertEqual((man["products"], man["prices"]), (2, 2))
        other = Database(self.tmp / "second.db")
        other.migrate()
        try:
            r1 = ref.import_bundle(other, path)
            r2 = ref.import_bundle(other, path)
            self.assertEqual((r1["products_created"], r1["prices_added"]), (2, 2))
            self.assertEqual((r2["products_created"], r2["products_updated"], r2["prices_added"]), (0, 0, 0))
            self.assertEqual(ref.by_barcode(other, "6221051011317")[0]["source_id"], "congestal-20-tabs")
            self.assertEqual(ref.summary(other), ref.summary(self.db))
            bad = self.tmp / "bad.zip"
            with zipfile.ZipFile(path) as z, zipfile.ZipFile(bad, "w") as w:
                w.writestr("manifest.json", z.read("manifest.json"))
                w.writestr("products.jsonl", z.read("products.jsonl").replace(b"5000", b"9000") + b" ")
            with self.assertRaises(ValueError):
                ref.import_bundle(other, bad)
        finally:
            other.close()

    def test_sync_full_then_changes_then_reload(self):
        from tests.test_reference_catalog import FakeResponse, FakeSession
        full = page(CONGESTAL)
        full["meta"]["generated_at"] = "2026-10-30T00:00:00Z"
        ev = {"data": [{"id": 1, "type": "price_changed", "drug_id": CONGESTAL["id"], "detected_at": "2026-10-30T05:00:00Z",
                        "data": {}, "drug": drug("congestal-20-tabs", "Congestal 20 tabs", price=60, eff="2026-10-30")}],
              "page": {"next_cursor": None}}
        too_old = FakeResponse(400, {"error": {"code": "since_too_old"}})
        s = FakeSession([FakeResponse(200, full), FakeResponse(200, ev), too_old, FakeResponse(200, full)])
        c = ref.DwapriceClient(api_key="dp_live_x", session=s, sleep=lambda x: None)
        self.assertEqual(ref.sync(self.db, c)["mode"], "full")
        self.assertEqual(ref.sync_state(self.db)["latest_detected_at"], "2026-10-30T00:00:00Z")
        r = ref.sync(self.db, c)
        self.assertEqual((r["mode"], r["events"]["applied"]), ("changes", 1))
        self.assertEqual(ref.sync(self.db, c)["mode"], "full")              # window expired -> reload
        self.assertEqual([h["price"] for h in ref.price_history(self.db, "congestal-20-tabs")], [5000, 6000])
