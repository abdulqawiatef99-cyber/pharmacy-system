"""Dead-stock report from E-Stock exports (synthetic files in the real layouts)."""
from datetime import date, datetime

from app.core.errors import ValidationFailed
from app.procurement import deadstock
from tests.test_api import APICase, H
from tests.test_estock import stock_rows, xlsx

SALES = [["ن.الربح", "إجمالى التكلفة", "إجمالى س.البيع", "سعر البيع", "الرصيد", "كمية البيع", "الشركة", "إسم الصنف", "ك.الصنف"],
         [25, 32, 40, 40, 2, 1, "الكان فارم", "ONE TWO THREE 20TAB", 1],       # 1 sold/yr, 2 left -> slow
         [25, "متوسط ن.الربح", 32, "إجمالى التكلفة", 40, "إجمالى سعر البيع", 1, "عدد الاصناف", None]]


def purchases(code_dates):
    rows = [["صيدلية"], ["الصيدلية", "اسم المخزن", 1, "كود المخزن"]]
    for i, (sup, code, d) in enumerate(code_dates):
        rows.append([sup, "الاسم", 1, "الكود"])
        rows.append(["الموظف"] + [None] * 12 + [d, 10, 0, 0, 0, 0, 10, 1, "آجل", str(i), i])
        rows.append([0, 0, 0, 10, 10, 0, 0, 0, 1, None, "علبة", "x", code])
    return rows


class TestDeadStock(APICase):
    def test_segments_and_adjustments_are_not_purchases(self):
        p = purchases([("ابن سينا", 30906, datetime(2026, 9, 1)), ("جرد", 1, datetime(2026, 10, 1))])
        r = deadstock.compute(xlsx(stock_rows()), xlsx(SALES), xlsx(p), as_of=date(2026, 10, 24))
        seg = {k: [x["code"] for x in v] for k, v in r["segments"].items()}
        self.assertEqual(seg["new"], [30906])          # no sale, bought last month
        self.assertEqual(seg["slow"], [1])             # 'جرد' ignored; sells 1/yr, 2 in stock = 24 months
        self.assertEqual(r["summary"][0]["segment"], "dead")

    def test_wrong_file_in_wrong_slot_is_refused(self):
        with self.assertRaises(ValidationFailed):
            deadstock.compute(xlsx(SALES), xlsx(stock_rows()), xlsx(purchases([])), as_of=date(2026, 10, 24))

    def test_api_json_and_xlsx(self):
        self.http_login()
        files = {"stock": ("s.xlsx", xlsx(stock_rows())), "sales": ("a.xlsx", xlsx(SALES)),
                 "purchases": ("p.xlsx", xlsx(purchases([("ابن سينا", 30906, datetime(2026, 9, 1))])))}
        r = self.c.post("/api/procurement/deadstock", headers=H, files=files)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["store"], "الصيدلية")
        r = self.c.post("/api/procurement/deadstock?format=xlsx", headers=H, files=files)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content[:2] == b"PK")
