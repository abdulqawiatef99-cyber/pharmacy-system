"""?limit= on list routes: a bad value is a 422 (it was an unhandled ValueError, a server error), and a
negative one is not SQLite's "no limit" (every item of the pharmacy in one answer). Synthetic data only."""
from app.catalog import service as cat
from tests.test_api import APICase


class QueryLimits(APICase):
    def setUp(self):
        super().setUp()
        self.http_login()
        for n in range(30):
            cat.create_item(self.db, self.ctx, {"name_en": f"Limit probe {n:02d}", "public_price": 100})

    def test_items_limit(self):
        self.assertEqual(self.c.get("/api/items?q=Limit&limit=abc").status_code, 422)
        self.assertEqual(self.c.get("/api/items?q=Limit&limit=2.5").status_code, 422)
        self.assertEqual(len(self.c.get("/api/items?q=Limit&limit=-1").json()), 1)
        self.assertEqual(len(self.c.get("/api/items?q=Limit&limit=5").json()), 5)
        self.assertEqual(len(self.c.get("/api/items?q=Limit").json()), 20)          # the default
        self.assertLessEqual(len(self.c.get("/api/items?q=Limit&limit=100000").json()), 200)

    def test_notifications_limit(self):
        self.assertEqual(self.c.get("/api/notifications?limit=abc").status_code, 422)
        self.assertEqual(self.c.get("/api/notifications?limit=-1").status_code, 200)
        self.assertEqual(self.c.get("/api/notifications").status_code, 200)
