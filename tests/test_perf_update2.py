"""U2-10: slow requests are logged (path only), and search stays fast at the size of a big pharmacy:
30,000 items and 5,000 customers. The search targets are the owner's: under 100 ms (median of 5, after a warm-up)."""
import statistics
import time

from app.api import app as appmod
from app.db import new_id
from tests.test_owner_plan_priority_1_2 import PlanCase

TARGET_SEARCH_MS = 100


class TestSlowLog(PlanCase):
    def test_slow_request_is_logged_without_query(self):
        appmod.SLOW_LOG.clear()
        old = appmod.SLOW_MS
        appmod.SLOW_MS = -1                      # every request counts as slow for this test
        try:
            self.call("GET", "/api/customers?q=01012345678")
        finally:
            appmod.SLOW_MS = old
        r = self.call("GET", "/api/system/slow-requests")
        hit = [x for x in r["requests"] if x["path"] == "/api/customers"]
        self.assertTrue(hit)
        self.assertNotIn("0101", str(r))


class TestSearchAtScale(PlanCase):
    def setUp(self):
        super().setUp()
        o = self.ctx.org_id
        with self.db.transaction():
            for n in range(30000):
                i = new_id()
                self.db.execute("INSERT INTO items (id, org_id, code, name_en, name_norm, pack_size, public_price,"
                                " status, dedupe_key) VALUES (?,?,?,?,?,1,1000,'active',?)",
                                (i, o, f"S{n}", f"Scale Item {n} 10mg", f"scale item {n} 10mg", i))
            for n in range(5000):
                self.db.execute("INSERT INTO customers (id, org_id, name, phone) VALUES (?,?,?,?)",
                                (new_id(), o, f"عميل {n}", f"010{n:08d}"))

    def median_ms(self, url):
        self.call("GET", url)                                  # warm-up
        times = []
        for _ in range(5):
            t = time.perf_counter()
            self.call("GET", url)
            times.append((time.perf_counter() - t) * 1000)
        return statistics.median(times)

    def test_item_and_customer_search_under_target(self):
        for url in ("/api/items?q=Scale%20Item%2029999", "/api/items?q=scale&stock=first",
                    "/api/customers?q=01000004999", "/api/customers?q=" + "عميل%204999"):
            ms = self.median_ms(url)
            self.assertLess(ms, TARGET_SEARCH_MS, f"{url}: {ms:.0f} ms")
