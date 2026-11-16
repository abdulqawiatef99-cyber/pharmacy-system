"""#206: every time shown (screens, receipts, printed pages, exports built from the screens) follows the pharmacy's
setting `time_format`: 12-hour with ص/م (the default) or 24-hour. Stored values stay UTC/ISO; only display changes."""
import json
import re
import shutil
import subprocess
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.inventory import service as inv
from app.printing import settings as rs
from tests.test_checkout_devices import H, CheckoutCase

JS = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
H12_LINES = "\n".join(re.search(rf"\nconst {n} = [^\n]*", JS).group(0) for n in ("LOC", "H12", "hhmm", "hourLabel", "dayOf"))

TWELVE = re.compile(r"\d{4}-\d{2}-\d{2} \d{1,2}:\d{2} [صم]")
TWENTY_FOUR = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?! [صم])")


class TestScreensFollowTheSetting(unittest.TestCase):
    def test_every_time_formatter_passes_the_setting(self):
        # a locale call without hour12 shows the locale's own clock (ar-EG 12-hour, en-GB 24-hour), not the setting
        for m in re.finditer(r"toLocale(?:Time)?String\([^\n]*", JS):
            self.assertIn("hour12: H12()", m.group(0), m.group(0))
        for raw in ('.slice(11, 16)', 'slice(0, 16).replace("T"', '.replace("T", " ")'):
            self.assertNotIn(raw, JS)                     # an ISO string cut by hand is UTC and 24-hour
        self.assertIn("const tm = (iso) => (iso ? hhmm(iso)", JS)
        self.assertIn("ME.time_format = f.time_format.value", JS)     # saving the setting applies it at once

    @unittest.skipUnless(shutil.which("node"), "Node not available")
    def test_helpers_in_both_modes(self):
        code = "const I18N = { on: false }; const isoDay = (d) => d.toISOString().slice(0, 10); let ME = null;\n" + H12_LINES + r"""
const t = "2026-11-13T22:06:00";   // local time, as the screens get it after parsing
const out = [];
for (const tf of [undefined, "12", "24"]) {
  ME = tf === undefined ? {} : { time_format: tf };
  out.push([hhmm(t), hourLabel(0), hourLabel(9), hourLabel(12), hourLabel(22)]);
}
ME = null; out.push([hhmm(t)]);                                       // before login: the default
console.log(JSON.stringify(out));
"""
        r = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        latin = lambda s: s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))  # noqa: E731
        for row in out[:2] + out[3:]:
            self.assertEqual(latin(row[0]), "10:06 م", row)                      # default and "12"
        self.assertEqual(out[0][1:], ["12ص", "9ص", "12م", "10م"])
        self.assertEqual(latin(out[2][0]), "22:06")
        self.assertEqual(out[2][1:], ["0", "9", "12", "22"])


class TestServerPagesFollowTheSetting(CheckoutCase):
    def test_me_carries_the_setting(self):
        self.assertEqual(self.c.get("/api/me").json()["time_format"], "12")
        rs.save(self.db, self.ctx, {"time_format": "24"})
        self.assertEqual(self.c.get("/api/me").json()["time_format"], "24")

    def shift_page(self):
        shift = self.db.one("SELECT id FROM cash_shifts WHERE status='open'")["id"]
        return self.c.get(f"/shift-report/{shift}", headers=H).text

    def test_shift_report_page(self):
        self.c.post("/api/print/drawer", json={"reason": "change"}, headers=H)
        page = self.shift_page()
        self.assertGreaterEqual(len(TWELVE.findall(page)), 2, page)        # opened at + the manual drawer line
        rs.save(self.db, self.ctx, {"time_format": "24"})
        page = self.shift_page()
        self.assertEqual(TWELVE.findall(page), [])
        self.assertGreaterEqual(len(TWENTY_FOUR.findall(page)), 2)

    def test_supplier_return_slip(self):
        with self.db.transaction():
            b = inv.get_or_create_batch(self.db, self.ctx, self.item, "Z1", date(2030, 1, 31).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.item, qty_delta=10, batch_id=b, movement_type="purchase",
                              unit_cost=Decimal("12"))
        sup = self.c.post("/api/suppliers", json={"name": "مورد تجربة"}, headers=H).json()
        r = self.c.post("/api/supplier-returns", json={"supplier_id": sup["id"], "lines": [
            {"item_id": self.item, "batch_id": b, "qty_base": 2, "reason": "damaged"}]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        url = f"/supplier-return/{r.json()['id']}"
        self.assertEqual(len(TWELVE.findall(self.c.get(url).text)), 1)
        rs.save(self.db, self.ctx, {"time_format": "24"})
        page = self.c.get(url).text
        self.assertEqual((TWELVE.findall(page), len(TWENTY_FOUR.findall(page))), ([], 1))


if __name__ == "__main__":
    unittest.main()
