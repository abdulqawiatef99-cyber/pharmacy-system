"""Order suggestion parameters (synthetic data)."""
from tests.test_api import APICase, H


class SuggestionParams(APICase):
    """Order suggestions: window_days=99999999 overflowed a date (500); 0 or negative days gave an empty list
    as if nothing was needed. Values outside the screen's own limits are refused."""

    def test_bounds(self):
        self.http_login()
        for path in ("/api/procurement/suggestions", "/api/procurement/suggestions.xlsx"):
            for q in ("window_days=99999999", "window_days=0", "window_days=-5", "cover_days=0", "cover_days=-30",
                      "cover_days=99999999999", "lead_days=-1", "safety_days=366", "lead_days=1e3", "cover_days=x"):
                with self.subTest(path=path, q=q):
                    self.assertEqual(self.c.get(f"{path}?{q}", headers=H).status_code, 422)
            for q in ("", "cover_days=1&lead_days=0&safety_days=0&window_days=7", "cover_days=730&window_days=3650"):
                with self.subTest(path=path, q=q):
                    self.assertEqual(self.c.get(f"{path}?{q}", headers=H).status_code, 200)
