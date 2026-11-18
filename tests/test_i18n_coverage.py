"""English mode: every Arabic text the program writes has an English key (owner/manager list 18/11, part D).
tests/i18n_gaps.js cuts the app's own trString() out of app.js, loads i18n-en.js, and runs every Arabic text
fragment of app.js / index.html through it. Only the texts below may stay Arabic: browser dialogs (prompt /
confirm are not page text), an Arabic search example, a file name, and pieces the extractor glues together."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWN = {
    "nnاكتب رقم الصنف:",                                      # prompt()
    "يوجد صنف مشابه: 0nهل أنت متأكد أنه صنف مختلف؟",            # confirm()
    "⛔ هذا البيع أقل من التكلفة (بخسارة):n0nnهل أنت متأكد أنك تريد المتابعة؟",   # confirm()
    "مثال: panadol أو بنادول أو asp%cid أو 622...",              # the example is an Arabic item name
    "ملاحظات-البرنامج-0.json",                                  # a download file name
    "و", "علبة0", "0 · 0 0 في العلبة · 0رصيد:",                # extractor joins: translated at run time
}


@unittest.skipUnless(shutil.which("node"), "Node not available")
class TestEnglishKeys(unittest.TestCase):
    def test_every_arabic_text_has_an_english_key(self):
        r = subprocess.run([shutil.which("node"), str(ROOT / "tests" / "i18n_gaps.js"), str(ROOT)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        left = sorted({f"{line}: {frag}" for line, frag, _ in json.loads(r.stdout) if frag not in KNOWN})
        self.assertEqual(left, [], "Arabic text with no English in app/web/i18n-en.js")

    def test_fixed_translations(self):
        js = (ROOT / "app" / "web" / "i18n-en.js").read_text(encoding="utf-8")
        for ar, en in {"لا يوجد حد طلب": "No reorder level", "ك.ب": "KB", "م.ب": "MB", "تم التسليم": "Delivered",
                       "وردت من": "Received from", "منذ": "since", "مكتملة": "Completed"}.items():
            self.assertIn(f'"{ar}": "{en}",', js)


if __name__ == "__main__":
    unittest.main()
