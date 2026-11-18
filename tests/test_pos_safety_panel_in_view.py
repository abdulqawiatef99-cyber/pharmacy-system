"""Heikal PC 17/11 (S1): on a short window the safety panel opened below the visible area, so «إتمام البيع»
seemed to do nothing. renderFindings must bring the panel into view and focus where the cashier acts."""
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "app" / "web" / "app.js"


class SafetyPanelInView(unittest.TestCase):
    def body(self):
        js = APP_JS.read_text(encoding="utf-8")
        start = js.index("function renderFindings(")
        return js[start:js.index("\nfunction ", start + 1)]

    def test_panel_scrolls_into_view(self):
        self.assertIn('$("#posSafety").scrollIntoView(', self.body())

    def test_focus_goes_to_the_first_reason_box_or_the_confirm_button(self):
        self.assertIn('($("#posSafety [data-dec]") || $("#posConfirm")).focus(', self.body())


if __name__ == "__main__":
    unittest.main()
