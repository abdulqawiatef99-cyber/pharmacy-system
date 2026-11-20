"""The report the owner reads on the trial (19/11): owner-report.txt. Arabic, plain words; what needs him first,
largest value first, each line saying what it is, E-Stock's figure, what the program did and the one thing he
must decide; how much matched and how much did not; the rest grouped by kind with counts. Synthetic data only."""
import re
from pathlib import Path

from tests.test_estock_db_import import Base, base_tables, product, stock_row

LABELS = ("في إي ستوك:", "البرنامج عمل:", "المطلوب منك:")


class _R(Base):
    def report(self, **kw) -> str:
        res = self.run_import(**kw)
        self.res = res
        return Path(res["reports"]["owner"]).read_text(encoding="utf-8-sig")

    @staticmethod
    def section(text: str, start: str, end: str | None) -> str:
        a = text.index(start)
        return text[a:text.index(end, a)] if end else text[a:]

    @staticmethod
    def entries(sec: str) -> list[str]:
        return re.split(r"\n(?=\s*\d+\) )", sec)[1:]


class TestOwnerReport(_R):
    def test_sections_in_order_and_the_result_first(self):
        t = self.report()
        self.assertTrue(t.startswith("تقرير نقل إي ستوك"))
        first = t.splitlines()[1]
        self.assertIn("النتيجة:", first)
        self.assertIn("اتنقل", first)
        a, b, c = (t.index(x) for x in ("١. محتاج قرارك", "٢. المطابقة", "٣. الباقي"))
        self.assertLess(a, b)
        self.assertLess(b, c)

    def test_owner_lines_largest_first_each_with_the_four_parts(self):
        tb = base_tables()
        tb["Purchase_header"][0]["class"] = "5"
        tb["Purchase_header"] += [{"purchase_id": "2", "vendor_id": "1", "class": "7", "total_bill": "7000.0000"}]
        t = self.report(tables=tb)
        sec = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        es = self.entries(sec)
        self.assertGreaterEqual(len(es), 3)
        vals = []
        for e in es:
            for lab in LABELS:
                self.assertIn(lab, e, e)
            m = re.search(r"القيمة ([\d,.]+) ج", e)
            vals.append(float(m.group(1).replace(",", "")) if m else 0.0)
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertIn("تحت التصريف", es[0])                       # the 7,000 purchase class (unknown 7) is the largest
        self.assertNotIn("OWNER", sec)                             # plain words, no internal tags

    def test_balances_to_confirm_is_an_owner_line(self):
        sec = self.section(self.report(), "١. محتاج قرارك", "٢. المطابقة")
        line = [e for e in self.entries(sec) if "أرصدة" in e][0]
        self.assertIn("أكّد", line)
        self.assertRegex(line, r"\d+ مورد")
        self.assertRegex(line, r"\d+ عميل")

    def test_matched_and_not_matched_are_visible(self):
        sec = self.section(self.report(), "٢. المطابقة", "٣. الباقي")
        for what in ("الأصناف", "المخزون", "الموردين", "العملاء", "التاريخ"):
            self.assertIn(what, sec)
        self.assertIn("100%", sec)
        self.assertRegex(sec, r"اتطابق \d+ من \d+")
        self.assertIn("الفرق", sec)
        self.assertIn("عدم مطابقة: مفيش", sec)

    def test_the_rest_is_grouped_by_kind_with_counts(self):
        tb = base_tables()
        tb["Customer"] += [{"customer_id": str(10 + i), "customer_code": f"X{i}", "customer_name_ar": f"عميل {i}",
                            "mobile": "12", "customer_current_money": "0", "deleted": "0", "active": "1"}
                           for i in range(3)]
        sec = self.section(self.report(tables=tb), "٣. الباقي", None)
        groups = [ln for ln in sec.splitlines() if re.match(r"\s*- .+: \d+ سطر", ln)]
        self.assertTrue(groups, sec)
        self.assertTrue(any(int(re.search(r": (\d+) سطر", g).group(1)) >= 3 for g in groups), groups)
        self.assertIn("data-quality.csv", sec)


class TestOwnerReportWhenBlocked(_R):
    def test_a_blocking_line_says_nothing_was_written_and_what_to_decide(self):
        tb = base_tables()
        tb["Products"].append(product(5, "104", "Testbroken Tab", product_unit3="3", product_unit1_3="2.5"))
        tb["Product_Amount"].append(stock_row(11, 5, 1, "2", "10.0000", "2027-01-01T00:00:00"))
        tb["Purchase_header"][0]["class"] = "7"                 # a 500 owner line that does not stop the move
        t = self.report(tables=tb)
        self.assertFalse(self.res["ok"])
        self.assertIn("وقف", t.splitlines()[1])
        sec = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        es = self.entries(sec)
        e = es[0]                                               # what stops the move comes first, whatever its value
        self.assertIn("104", e)
        self.assertIn("وقف النقل", e)
        self.assertIn("كام وحدة", e)
        self.assertIn("معامل الوحدة الصغرى", e)


class TestBatShowsTheOwnerReport(_R):
    def test_opened_after_the_dry_run_before_yes_and_on_failure(self):
        root = Path(__file__).resolve().parents[1]
        lines = [l.strip() for l in (root / "scripts/windows/estock-db-import.bat").read_text(encoding="utf-8").splitlines()]
        show = lines.index('call :owner "%REP%-dry"')
        yes = next(i for i, l in enumerate(lines) if 'set /p "ANS=Type YES' in l)
        self.assertLess(show, yes)
        fail = lines.index(":fail")
        self.assertTrue(any(":owner" in l for l in lines[fail:fail + 4]))      # when the move stops, he sees why
        sub = lines[lines.index(":owner"):]
        self.assertIn("notepad", " ".join(sub[:8]))
        self.assertIn("PHARM_E2E_ANSWER", " ".join(sub[:8]))                    # never opens in automated runs
        self.assertTrue(any('del /q "%OUT%\\owner-report.txt"' in l for l in lines))  # an earlier run's report is not this one


class TestCodexReview361(_R):
    """Codex on #361 @0feea2c (each failed before the fix)."""

    def test_p2_a_failed_apply_shows_its_own_report_not_the_trials(self):
        root = Path(__file__).resolve().parents[1]
        lines = [l.strip() for l in (root / "scripts/windows/estock-db-import.bat").read_text(encoding="utf-8").splitlines()]
        fail = lines[lines.index(":fail"):lines.index(":fail") + 5]
        applied = next(i for i, l in enumerate(fail) if 'call :owner "%REP%"' in l and "-dry" not in l)
        self.assertIn('if exist "%REP%\\owner-report.txt"', fail[applied])
        trial = next(i for i, l in enumerate(fail) if 'call :owner "%REP%-dry"' in l)
        self.assertLess(applied, trial)
        self.assertIn('if not exist "%REP%\\owner-report.txt"', fail[trial])

    def test_p2_b_an_unclear_drawer_is_pending_not_posted(self):
        from app.dataimport import estock_owner_report as r
        d = {"table": "Cash_depots", "estock_id": "1", "code": "x", "field": "last_close", "estock_value": "1, 2",
             "action": "OWNER: مش واضح أنهي درج هو درج الشيفت (اتقفل فيه شيفتات)", "value_egp": "10.00",
             "severity": "review"}
        _, _, done = r._ask(d)
        self.assertNotIn("اتعمل حساب", done)
        self.assertIn("ما اتنقلش", done)
        self.assertIn("F105", done)


class TestCodexReview361b(_R):
    """Codex on #361 @f65cb69: money waiting for the accounts service (F105) is never hidden in a group."""

    def test_p2_money_not_moved_yet_is_an_owner_line_and_the_result_says_so(self):
        tb = base_tables()
        tb["Cash_depots"] = [{"cd_id": "1", "cd_name": "الخزنة الرئيسية", "cd_class": "1", "cd_balance": "1200.0000"},
                             {"cd_id": "2", "cd_name": "فودافون كاش", "cd_class": "1", "cd_balance": "500.0000"}]
        from unittest import mock
        from app.dataimport import estock_db
        with mock.patch.object(estock_db, "_has_money", return_value=False):    # the schema before 0038
            t = self.report(tables=tb)
        self.assertTrue(self.res["ok"])
        first = t.splitlines()[1]
        self.assertNotIn("كل حاجة اتنقلت", first)
        self.assertIn("الخزنة والمحافظ", first)
        sec = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        e = [x for x in self.entries(sec) if "الخزنة والمحافظ" in x][0]
        self.assertIn("500.00", e)                                 # owner 20/11 00:50: the treasury is a reference
        self.assertNotIn("1,200.00", e)
        self.assertIn("ما اتنقلتش", e)
        self.assertIn("F105", e)
        self.assertIn("فودافون كاش", e)
        self.assertIn("مرجع", self.section(t, "٣. الباقي", None))
        margin = self.section(t, "٢. المطابقة", "٣. الباقي")
        self.assertIn("لسه ما اتنقلتش", margin)


class TestCodexReview361c(_R):
    """Codex on #361 @b27c26a: the balances line shows E-Stock's own total and the owner's corrections apart."""

    def test_p2_estock_total_and_confirmed_adjustments_apart(self):
        from openpyxl import load_workbook
        from tests.helpers import DBTestCase
        res = self.run_import()
        wb = load_workbook(res["reports"]["openings"])
        ws = wb.active
        for r in ws.iter_rows(min_row=2):
            if r[1].value == "1" and r[0].value == "مورد":
                r[5].value = 450                                     # E-Stock 500, the owner says 450
        conf = self.tmp / "confirmed.xlsx"
        wb.save(conf)
        self.db.close()
        DBTestCase.setUp(self)
        t = self.report(confirm_file=conf)
        sec = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        e = [x for x in self.entries(sec) if "أرصدة" in x][0]
        self.assertIn("655.50", e)                                   # E-Stock's own total, as in the workbook
        self.assertIn("50.00", e)                                    # the owner's correction, shown apart
        self.assertIn("أكدته", e)


class TestManager0025(_R):
    """Manager 20/11 00:25 after the cashier-PC rehearsal (each failed before the change)."""

    def test_a_every_owner_line_names_the_item_with_its_code(self):
        tb = base_tables()
        tb["Products"].append(product(5, "104", "Testbroken Tab", "دواء تجربة مكسور", product_unit3="3",
                                      product_unit1_3="2.5"))
        tb["Product_Amount"].append(stock_row(11, 5, 1, "2", "10.0000", "2027-01-01T00:00:00"))
        sec = self.section(self.report(tables=tb), "١. محتاج قرارك", "٢. المطابقة")
        e = [x for x in self.entries(sec) if "104" in x][0]
        self.assertIn("«Testbroken Tab»", e)
        self.assertIn("كود 104", e)
        self.assertIn("packs.json", e)

    def test_b_classes_say_what_each_choice_does_and_are_answered_in_the_decision_file(self):
        import json
        tb = base_tables()
        tb["Purchase_header"][0]["class"] = "7"
        t = self.report(tables=tb)
        sec = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        e = [x for x in self.entries(sec) if "class" in x][0]
        for choice in ("كاش", "آجل", "تحت التصريف", '"classes"'):
            self.assertIn(choice, e)
        dec = self.tmp / "packs.json"
        dec.write_text(json.dumps({"classes": {"7": "تحت التصريف"}}, ensure_ascii=False), encoding="utf-8")
        self.db.close()
        from tests.helpers import DBTestCase
        DBTestCase.setUp(self)
        t2 = self.report(tables=tb, packs_file=dec)
        sec2 = self.section(t2, "١. محتاج قرارك", "٢. المطابقة")
        self.assertNotIn("class", sec2)                                  # answered: out of section 1
        self.assertEqual(self.res["plan"].checks["purchase_classes"]["7"]["meaning"], "consignment")

    def test_c_a_big_sale_line_is_archive_only_and_not_in_section_1(self):
        tb = base_tables()
        tb["Branches_sales_details"][0].update({"total_sell": "25000.0000"})
        t = self.report(tables=tb)
        self.assertNotIn("سطر بيع", self.section(t, "١. محتاج قرارك", "٢. المطابقة"))
        self.assertIn("سطر بيع", self.section(t, "٣. الباقي", None))


class TestCodexReview384(_R):
    """Codex on #384 @b78aada (each failed before the fix)."""

    def test_p2_a_stock_of_a_missing_product_is_not_named_after_another_item(self):
        tb = base_tables()
        tb["Product_Amount"].append(stock_row(12, 100, 1, "2", "10.0000", "2027-01-01T00:00:00"))  # id 100: no product
        t = self.report(tables=tb)
        line = [x for x in self.entries(self.section(t, "١. محتاج قرارك", "٢. المطابقة")) if "ممسوح" in x]
        self.assertTrue(line, t)
        self.assertNotIn("Testamol", line[0])                            # code 100 is another item (product id 1)
        self.assertIn("رقم 100", line[0])

    def test_p2_b_class_answers_in_packs_json_count_when_packs_xlsx_is_chosen(self):
        import json
        from openpyxl import Workbook
        tb = base_tables()
        tb["Purchase_header"][0]["class"] = "7"
        wb = Workbook()
        wb.active.append(["الكود", "عدد الوحدات"])
        wb.active.append(["100", 3])
        xl = self.tmp / "packs.xlsx"
        wb.save(xl)
        (self.tmp / "packs.json").write_text(json.dumps({"classes": {"7": "آجل"}}, ensure_ascii=False), encoding="utf-8")
        self.report(tables=tb, packs_file=xl)
        self.assertEqual(self.res["plan"].checks["purchase_classes"]["7"]["meaning"], "credit")


class TestOwner0050(_R):
    """Owner 20/11 00:50, relayed by the manager (each failed before the change)."""

    def test_classes_1_2_3_are_the_owners_decision_without_a_file(self):
        tb = base_tables()
        tb["Purchase_header"][0]["class"] = "2"
        tb["Purchase_header"] += [{"purchase_id": "2", "vendor_id": "1", "class": "1", "total_bill": "70.0000"},
                                  {"purchase_id": "3", "vendor_id": "1", "class": "3", "total_bill": "90.0000"}]
        t = self.report(tables=tb)
        self.assertNotIn("class", self.section(t, "١. محتاج قرارك", "٢. المطابقة"))
        pc = self.res["plan"].checks["purchase_classes"]
        self.assertEqual({c: pc[c]["meaning"] for c in ("1", "2", "3")},
                         {"1": "cash", "2": "credit", "3": "consignment"})

    def test_a_cancelled_item_with_unclear_units_and_no_stock_moves_stopped_and_is_listed(self):
        tb = base_tables()
        tb["Products"].append(product(5, "104", "Testcancelled Tab", "صنف ملغي", product_unit3="3",
                                      product_unit1_3="2.5", deleted="1"))
        t = self.report(tables=tb)
        self.assertTrue(self.res["ok"], self.res.get("plan").blocking)
        it = self.db.one("SELECT * FROM items WHERE code='104'")
        self.assertTrue(it)
        self.assertEqual(it["status"], "inactive")
        self.assertNotIn("104", self.section(t, "١. محتاج قرارك", "٢. المطابقة"))
        doubt = self.section(t, "أصناف مشكوك فيها", None)
        self.assertIn("«Testcancelled Tab»", doubt)
        self.assertIn("كود 104", doubt)

    def test_a_cancelled_item_with_unclear_units_and_stock_still_stops(self):
        tb = base_tables()
        tb["Products"].append(product(5, "104", "Testcancelled Tab", "صنف ملغي", product_unit3="3",
                                      product_unit1_3="2.5", deleted="1"))
        tb["Product_Amount"].append(stock_row(11, 5, 1, "2", "10.0000", "2027-01-01T00:00:00"))
        self.report(tables=tb)
        self.assertFalse(self.res["ok"])                                  # its stock needs the real pack

    def test_the_treasury_is_a_reference_figure_not_money(self):
        tb = base_tables()
        tb["Cash_depots"] = [{"cd_id": "1", "cd_name": "الخزينة", "cd_class": "1", "cd_balance": "1200.0000"},
                             {"cd_id": "2", "cd_name": "فودافون كاش", "cd_class": "1", "cd_balance": "500.0000"}]
        t = self.report(tables=tb)
        d = [x for x in self.res["plan"].checks["money_info"]["cash_depots"] if x["estock_id"] == "1"][0]
        self.assertTrue(d.get("reference_only"))
        sec1 = self.section(t, "١. محتاج قرارك", "٢. المطابقة")
        self.assertNotIn("1,200.00", sec1)                                # not counted as money to move
        self.assertIn("500.00", sec1)
        self.assertIn("مرجع", t)
        self.assertIn("1,200.00", t)
