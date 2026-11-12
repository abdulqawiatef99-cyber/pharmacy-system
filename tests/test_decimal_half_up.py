"""Decimal.to_integral_value() and .quantize(q) round half to EVEN unless told otherwise. AGENTS.md: money is
rounded half up (discount_of). A supplier return of a unit costing 0.125 pounds was valued 12 piasters
instead of 13, and a stock adjustment the same. Every rounding Decimal call in app/ now names its rounding;
a sweep keeps it so. Synthetic data only."""
import ast
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.inventory import adjust
from app.inventory import service as inv
from app.procurement import returns
from tests.helpers import DBTestCase

APP = Path(__file__).resolve().parent.parent / "app"


def unnamed_roundings() -> list[str]:
    """Calls that round with the context default (half to even). A call inside a comparison only asks
    "is it whole?", which no rounding mode changes, so it is allowed."""
    bad = []
    for p in sorted(APP.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        in_compare = {id(m) for n in ast.walk(tree) if isinstance(n, ast.Compare) for m in ast.walk(n)}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("to_integral_value", "to_integral", "quantize")):
                positional = 1 if n.func.attr == "quantize" else 0
                if (len(n.args) <= positional and not any(k.arg == "rounding" for k in n.keywords)
                        and id(n) not in in_compare):
                    bad.append(f"{p.relative_to(APP.parent)}:{n.lineno}")
            # round(<a Decimal>) is half to even too
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "round" and n.args
                    and any(isinstance(m, ast.Call) and isinstance(m.func, ast.Name) and m.func.id == "Decimal"
                            for m in ast.walk(n.args[0]))):
                bad.append(f"{p.relative_to(APP.parent)}:{n.lineno}")
    return bad


class Sweep(unittest.TestCase):
    def test_every_decimal_rounding_names_its_mode(self):
        self.assertEqual(unnamed_roundings(), [])


class HalfUpValues(DBTestCase):
    def setUp(self):
        super().setUp()
        self.it = cat.create_item(self.db, self.ctx, {"name_en": "Eighth Cost", "pack_size": 1, "public_price": 100})
        with self.db.transaction():
            self.b = inv.get_or_create_batch(self.db, self.ctx, self.it, "H1",
                                             (date.today() + timedelta(days=300)).isoformat())
            inv.post_movement(self.db, self.ctx, item_id=self.it, qty_delta=4, batch_id=self.b,
                              movement_type="purchase", unit_cost=Decimal("0.125"))

    def test_supplier_return_value_is_half_up(self):
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, self.b), 13)     # 12.5 -> 13, not 12
        self.assertEqual(returns.suggest_value(self.db, self.ctx, self.it, None), 13)       # from the average cost

    def test_stock_adjustment_value_is_half_up(self):
        up = adjust.adjust(self.db, self.ctx, self.it, qty_delta=1, reason="found")
        self.assertEqual(up["value"], 13)
        down = adjust.adjust(self.db, self.ctx, self.it, qty_delta=-1, batch_id=self.b, reason="damaged")
        self.assertEqual(down["value"], -13)
