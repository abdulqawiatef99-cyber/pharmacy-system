"""Owner 17/11: the scale test covers every pharmacy size, the largest included (over a million EGP a day)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "perf"))
import real_years  # noqa: E402


class Profiles(unittest.TestCase):
    def test_sizes_grow_from_small_to_giant(self):
        self.assertEqual(list(real_years.PROFILES), ["small", "medium", "large", "huge", "giant"])
        lows = [int(p["per_day"].split("-")[0]) for p in real_years.PROFILES.values()]
        self.assertEqual(lows, sorted(lows))
        items = [p["items"] for p in real_years.PROFILES.values()]
        self.assertEqual(items, sorted(items))

    def test_giant(self):
        self.assertEqual(real_years.PROFILES["giant"], {"per_day": "6000-9000", "items": 45000,
                                                        "customers": 10000, "suppliers": 60})


if __name__ == "__main__":
    unittest.main()
