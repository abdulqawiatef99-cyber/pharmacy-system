import copy
import json
import re
import unittest

from app import reference


class ReferenceDataTest(unittest.TestCase):
    def setUp(self):
        self.data = reference.load()

    def test_file_is_consistent(self):
        self.assertEqual(reference.validate(self.data), [])

    def test_validator_catches_broken_links(self):
        bad = copy.deepcopy(self.data)
        bad["cross_sell"][0]["suggest"][0]["cat"] = "no_such_category"
        bad["safety_alerts"][0]["src"] = ["S999"]
        errs = reference.validate(bad)
        self.assertTrue(any("no_such_category" in e for e in errs))
        self.assertTrue(any("source" in e for e in errs))

    def test_egypt_first(self):
        meta = self.data["meta"]
        self.assertTrue(any("هيئة الدواء المصرية" in r and "المرجع الأول" in r for r in meta["rules"]))
        eg = reference.egypt_sources(self.data)
        self.assertGreaterEqual(len(eg), 7)
        self.assertTrue(all("edaegypt.gov.eg" in s["url"] for s in eg))
        self.assertIn(self.data["egypt_otc"]["source"], {s["id"] for s in eg})
        self.assertIn(self.data["egypt_do_not_crush"]["source"], {s["id"] for s in eg})

    def test_nothing_activates_by_itself(self):
        # every safety item carries a source and an engine flag; the loader writes nothing
        for r in self.data["safety_alerts"]:
            self.assertTrue(r["src"])
            self.assertIn("engine", r)
        self.assertIn("اعتماد", self.data["meta"]["status"])
        self.assertIn("آليًا", self.data["egypt_otc"]["note"])
        self.assertIn("آليًا", self.data["egypt_do_not_crush"]["note"])

    def test_no_third_party_licensed_text_markers(self):
        raw = json.dumps(self.data, ensure_ascii=False)
        self.assertNotRegex(raw, re.compile(r"TBP-[A-Z]{4}|The Best Pharmacist", re.I))

    def test_readable_copy_matches_version(self):
        md = reference.PATH.with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn(self.data["meta"]["version"], md)


if __name__ == "__main__":
    unittest.main()
