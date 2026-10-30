"""SYNTHETIC backup produced by an older release, for tests/test_legacy_restore.py.

Run it from a checkout of the release that pharmacies run (the code that writes the
backup must be the old code, not this branch's):

    git worktree add /tmp/rel origin/main
    cd /tmp/rel && PYTHONPATH=. python <this repo>/tests/fixtures/make_legacy_backup.py <out dir>

then copy the ZIP to tests/fixtures/legacy_backup_<commit>.zip. No real pharmacy data:
one test pharmacy, one item, one cash sale with a transfer screenshot, a closed shift.
"""
import base64
import sys
from pathlib import Path

from app.core import backup
from app.sales import attachments
from tests.test_cashier_acceptance import Base

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class _Data(Base):
    def runTest(self):
        pass


def main(out: Path) -> Path:
    d = _Data()
    d.setUp()
    d.cd.open(d.ctx, 10000)
    sale = d.sell(units=4)
    attachments.attach(d.db, d.tmp / "uploads", d.ctx, sale["id"], PNG, "transfer.png")
    d.cd.close(d.ctx, d.cd.summary(d.ctx)["expected_cash"])
    return backup.create(d.db, d.tmp / "uploads", out)


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
