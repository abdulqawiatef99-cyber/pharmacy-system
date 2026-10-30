"""SYNTHETIC split-pack sales written by the release pharmacies run, for tests/test_legacy_split_refund.py (#72).

Run on the OLD code (CI checks out main into _release and sets PHARM_RELEASE_SRC):

    cd <release checkout> && PYTHONPATH=. python <this repo>/tests/fixtures/make_legacy_split_sale.py <out dir>

One pack of 3 (100.00) sold from two batches of the same price (FEFO: 2 + 1 units), once paid in cash
and once on a customer's account. The release priced each batch on its own: 2 x 33.33 + 33.33 = 99.99.
Prints the database path; the sale ids are in the JSON file next to it. No real data.
"""
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.catalog import service as cat
from app.inventory import service as inv
from tests.test_cashier_acceptance import Base


class _Data(Base):
    def runTest(self):
        pass


def main(out: Path) -> Path:
    d = _Data()
    d.setUp()
    try:
        d.cd.open(d.ctx, 0)
        cid = d.s.create_customer(d.ctx, name="عميل تجريبي", credit_limit=100000)
        ids = {}
        for key, customer, credit in (("cash", None, False), ("credit", cid, True)):
            it = cat.create_item(d.db, d.ctx, {"name_en": f"Split Pack 3 {key}", "pack_size": 3, "public_price": 10000})
            with d.db.transaction():
                for no, days, qty in (("A", 200, 2), ("B", 400, 30)):
                    b = inv.get_or_create_batch(d.db, d.ctx, it, no, (date.today() + timedelta(days=days)).isoformat())
                    inv.post_movement(d.db, d.ctx, item_id=it, qty_delta=qty, batch_id=b, movement_type="purchase",
                                      unit_cost=Decimal(20))
            sid = d.s.create(d.ctx, customer)
            total = d.s.add_line(d.ctx, sid, it, units=3)["total"]      # 9999 under the release
            f = d.s.safety_check(d.ctx, sid)
            d.s.complete(d.ctx, sid, paid_cash=0 if credit else total, paid_card=0, on_credit=total if credit else 0,
                         safety_decisions={i: "ok" for i in range(len(f))})
            ids[key] = sid
        out.mkdir(parents=True, exist_ok=True)
        target = out / "legacy-split.db"
        d.db.backup_to(target)
        (out / "legacy-split.json").write_text(json.dumps(ids))
        return target
    finally:
        d.tearDown()


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
