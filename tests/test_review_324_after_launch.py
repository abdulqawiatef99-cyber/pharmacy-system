"""Codex review #324 parts 2-4, group D (after launch, owner decides): reproductions of the findings that
are verified but not fixed before launch. (S5/X11, S19, S28-S32 were fixed on 19/11: their tests moved to
tests/test_review_324_group_d.py; S3, S4, S36-S40 the same day:
tests/test_review_324_stock_p2.py.) Each test states the correct behaviour and is marked
expectedFailure: it fails today, and the suite turns red ("unexpected success") the day the defect is
fixed, so the mark is removed with the fix. Synthetic data only."""
import unittest

from app.purchasing import validation

known = unittest.expectedFailure


class TestS22InferredNetRounding(unittest.TestCase):
    @known
    def test_inferred_net_uses_the_discount_of_rule(self):
        from app.sales.service import discount_of
        line = validation.derive_line({"public_price": 101, "discount_bp": 5000, "qty": 1,
                                 "field_sources": "{}", "field_confidence": "{}"})
        self.assertEqual(line["unit_net_price"], 101 - discount_of(101, 5000))


if __name__ == "__main__":
    unittest.main()
